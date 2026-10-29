"""预测缓存：JSONL.gz，按 (videoId, modelHash, sampleFps, confHash) 索引。

调参时不重复跑模型；缓存文件写完后原子替换，中断不会污染既有缓存。
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from typing import Any, Iterable, Iterator

from .contracts import FrameObservation


def _config_fingerprint(extra: dict[str, Any]) -> str:
    payload = json.dumps(extra, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(payload.encode()).hexdigest()[:12]


def cache_path(cache_dir: str, video_id: str, model_hash: str, fingerprint: str) -> str:
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, f"{video_id}.{model_hash}.{fingerprint}.jsonl.gz")


def fingerprint(sample_fps: float, conf: float, iou: float, tracker: str) -> str:
    return _config_fingerprint({"sampleFps": sample_fps, "conf": conf, "iou": iou, "tracker": tracker})


def read_cache(path: str) -> Iterator[FrameObservation]:
    if not os.path.isfile(path):
        return
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            yield FrameObservation.model_validate(obj)


def write_cache_atomic(path: str, frames: Iterable[FrameObservation]) -> int:
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".cache-", suffix=".jsonl.gz", dir=os.path.dirname(path) or ".")
    os.close(tmp_fd)
    count = 0
    try:
        with gzip.open(tmp_path, "wt", encoding="utf-8") as f:
            for fr in frames:
                f.write(json.dumps(fr.model_dump(by_alias=True), ensure_ascii=False, separators=(",", ":")))
                f.write("\n")
                count += 1
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise
    return count
