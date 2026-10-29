"""Pose 推理适配：独立 YOLO 实例 + ByteTrack，每个文件独立跟踪上下文。

不复用 `backend.app.infer_runtime.UltralyticsRuntime`，原因：
1. 该运行时一次仅允许一个全局模型，且 tracker 上下文与在线 WS/RTSP 共享；
2. 离线批处理需要"每文件独立 persist 状态，文件结束即释放"，直接实例化
   YOLO 更简单、更安全，也不受全局 predict_lock 影响；
3. 权重文件与运行时保持一致（`backend/models/yolo11m-pose.pt`）。
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any

import numpy as np
from ultralytics import YOLO

from .contracts import Keypoint, PersonObservation


@dataclass
class PoseAdapter:
    model_path: str
    device: str = "cpu"
    conf: float = 0.25
    iou: float = 0.5
    tracker_yaml: str = "bytetrack.yaml"
    class_name: str = "person"
    _model: Any = None
    _model_hash: str | None = None

    # ------------------------------------------------------------------
    def load(self) -> None:
        if self._model is None:
            self._model = YOLO(self.model_path)
            self._model_hash = _sha1(self.model_path)

    @property
    def model_hash(self) -> str | None:
        if self._model_hash is None:
            self._model_hash = _sha1(self.model_path)
        return self._model_hash

    # ------------------------------------------------------------------
    def infer(self, image_bgr: np.ndarray, *, track: bool = True) -> list[PersonObservation]:
        """单帧推理。image_bgr: HxWx3 uint8（OpenCV BGR）。"""
        self.load()
        kwargs: dict[str, Any] = dict(
            verbose=False,
            device=self.device,
            conf=self.conf,
            iou=self.iou,
        )
        if track:
            results = self._model.track(image_bgr, persist=True, tracker=self.tracker_yaml, **kwargs)
        else:
            results = self._model.predict(image_bgr, **kwargs)
        if not results:
            return []
        r0 = results[0]
        persons: list[PersonObservation] = []
        boxes = getattr(r0, "boxes", None)
        if boxes is None:
            return persons
        xyxy = _np(boxes.xyxy)
        confs = _np(boxes.conf)
        clss = _np(boxes.cls)
        ids = _np(getattr(boxes, "id", None)) if track else None
        kps_obj = getattr(r0, "keypoints", None)
        kp_xy = _np(kps_obj.xy) if kps_obj is not None else None
        kp_conf = _np(getattr(kps_obj, "conf", None)) if kps_obj is not None else None

        names = getattr(self._model, "names", {}) or {}
        for i in range(len(xyxy)):
            cls_id = int(clss[i])
            cls_name = names.get(cls_id, str(cls_id)) if isinstance(names, dict) else str(cls_id)
            if cls_name != self.class_name:
                continue
            x1, y1, x2, y2 = [float(v) for v in xyxy[i]]
            tid: int | None = None
            if ids is not None and i < len(ids):
                try:
                    tid = int(ids[i])
                except Exception:
                    tid = None
            keypoints: list[Keypoint] = []
            if kp_xy is not None and i < len(kp_xy):
                joints = kp_xy[i]
                confs_j = kp_conf[i] if kp_conf is not None and i < len(kp_conf) else None
                for j, (jx, jy) in enumerate(joints):
                    jc = float(confs_j[j]) if confs_j is not None and j < len(confs_j) else None
                    keypoints.append(Keypoint(x=float(jx), y=float(jy), conf=jc))
            persons.append(
                PersonObservation(
                    track_id=tid,
                    bbox=[x1, y1, x2, y2],
                    bbox_conf=float(confs[i]),
                    cls=cls_name,
                    keypoints=keypoints,
                )
            )
        return persons

    # ------------------------------------------------------------------
    def close(self) -> None:
        self._model = None


def _np(t):
    if t is None:
        return None
    if hasattr(t, "cpu"):
        return t.cpu().numpy()
    return np.asarray(t)


def _sha1(path: str, chunk: int = 1 << 20) -> str:
    if not os.path.isfile(path):
        return ""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()[:16]


def resolve_model_path(model_id: str, candidates: list[str] | None = None) -> str:
    """在 backend/models 与常见位置解析模型文件。"""
    if os.path.isabs(model_id) and os.path.isfile(model_id):
        return model_id
    here = os.path.dirname(os.path.abspath(__file__))
    roots = [
        os.path.abspath(os.path.join(here, "..", "..", "..", "backend", "models")),
        os.path.abspath(os.path.join(here, "..", "..", "..", "..", "backend", "models")),
    ]
    for extra in candidates or []:
        roots.append(extra)
    for r in roots:
        p = os.path.join(r, model_id)
        if os.path.isfile(p):
            return p
    raise FileNotFoundError(f"Model not found: {model_id} (searched {roots})")
