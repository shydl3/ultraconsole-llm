"""离线分析主流程：把视频/配置/推理/规则/报告串起来。

单个视频的处理管线：
1. probe → 决定 camera profile（按文件名或显式指定）
2. 逐帧解码 + Pose 推理（或从缓存加载）→ FrameObservation 序列
3. 依次喂入 detectors + workflow
4. finalize → 事件列表
5. 写 run.json / events.json / events.csv / per-video/{videoId}.json / frames/*.jpg
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np

from . import cache as cache_mod
from . import reports, video_reader
from .contracts import (
    CameraProfile,
    Event,
    FrameObservation,
    PersonObservation,
    RulesConfig,
    RunSummary,
    VideoReport,
)
from .detectors import (
    AccessCrossingDetector,
    HandNearEarDetector,
    PostVacancyDetector,
    SlumpedDetector,
)
from .pose_adapter import PoseAdapter, resolve_model_path
from .quality import frame_quality
from .workflows import PassageWorkflow


VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm")


@dataclass
class AnalyzeOptions:
    input_path: str
    config_path: str
    output_dir: str
    run_id: str | None = None
    cache_dir: str | None = None
    device: str | None = None
    profile_id: str | None = None
    profile_map: dict[str, str] = field(default_factory=dict)  # filename substring -> profile id
    save_evidence: bool = True
    max_evidence_per_rule: int = 3
    use_cache: bool = True
    write_cache: bool = True
    progress_every: int = 50


def _config_hash(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()[:16]


def load_config(path: str) -> RulesConfig:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return RulesConfig.model_validate(raw)


def collect_videos(input_path: str) -> list[str]:
    if os.path.isfile(input_path):
        return [input_path] if input_path.lower().endswith(VIDEO_EXTS) else []
    if os.path.isdir(input_path):
        out: list[str] = []
        for name in sorted(os.listdir(input_path)):
            p = os.path.join(input_path, name)
            if os.path.isfile(p) and p.lower().endswith(VIDEO_EXTS):
                out.append(p)
        return out
    return []


def pick_profile(cfg: RulesConfig, video_path: str, opts: AnalyzeOptions) -> CameraProfile | None:
    fname = os.path.basename(video_path)
    for substr, pid in (opts.profile_map or {}).items():
        if substr and substr in fname:
            prof = cfg.profile(pid)
            if prof is not None:
                return prof
    if opts.profile_id:
        return cfg.profile(opts.profile_id)
    if cfg.default_profile_id:
        return cfg.profile(cfg.default_profile_id)
    return cfg.camera_profiles[0] if cfg.camera_profiles else None


# ---------------------------------------------------------------------------
# 单视频处理
# ---------------------------------------------------------------------------


def _iter_observations(
    video_path: str,
    cfg: RulesConfig,
    profile: CameraProfile | None,
    run_id: str,
    adapter: PoseAdapter,
    opts: AnalyzeOptions,
    meta: video_reader.VideoMeta,
) -> tuple[Iterable[FrameObservation], video_reader.ReadReport, str | None]:
    """返回 (观测迭代器, 读取报告, 缓存路径)。命中缓存时不重新推理。"""
    fp = cache_mod.fingerprint(cfg.thresholds.sample_fps, cfg.conf, cfg.iou, adapter.tracker_yaml)
    cache_path = (
        cache_mod.cache_path(opts.cache_dir or "", meta.video_id, adapter.model_hash or "", fp)
        if opts.cache_dir
        else None
    )
    read_report = video_reader.ReadReport()

    if opts.use_cache and cache_path and os.path.isfile(cache_path):
        frames = list(cache_mod.read_cache(cache_path))
        read_report.sampled_frames = len(frames)
        if frames:
            read_report.time_source = frames[0].time_source
        return frames, read_report, cache_path

    def gen() -> Iterable[FrameObservation]:
        produced: list[FrameObservation] = []
        for df in video_reader.iter_frames(video_path, sample_fps=cfg.thresholds.sample_fps, meta=meta, report=read_report):
            persons: list[PersonObservation] = adapter.infer(df.image_bgr, track=True)
            frame = FrameObservation(
                run_id=run_id,
                video_id=meta.video_id,
                camera_profile_id=(profile.id if profile else None),
                frame_index=df.frame_index,
                media_time_sec=df.media_time_sec,
                width=meta.width,
                height=meta.height,
                time_source=df.time_source,  # type: ignore[arg-type]
                persons=persons,
                quality=frame_quality(persons, min_conf=cfg.thresholds.keypoint_conf),
            )
            produced.append(frame)
            if opts.progress_every and len(produced) % opts.progress_every == 0:
                print(f"  [{meta.video_id}] frame {len(produced)} t={df.media_time_sec:.1f}s persons={len(persons)}")
            yield frame
        if opts.write_cache and cache_path:
            cache_mod.write_cache_atomic(cache_path, produced)

    return gen(), read_report, cache_path


def _make_detectors(cfg: RulesConfig, profile: CameraProfile) -> list[Any]:
    out: list[Any] = []
    for rule_id, cls in (
        ("post.vacant", PostVacancyDetector),
        ("access.crossing", AccessCrossingDetector),
        ("pose.slumped", SlumpedDetector),
        ("pose.hand_near_ear", HandNearEarDetector),
    ):
        dcfg = cfg.detectors.get(rule_id)
        if dcfg is None or not dcfg.enabled:
            continue
        try:
            det = cls(cfg, profile, dcfg.params)
        except Exception as e:  # noqa: BLE001
            print(f"  ! detector {rule_id} init failed: {e}")
            continue
        if hasattr(det, "enabled") and not det.enabled:
            continue
        out.append(det)
    return out


def _make_workflow(cfg: RulesConfig, profile: CameraProfile) -> PassageWorkflow | None:
    dcfg = cfg.detectors.get("passage.check")
    if dcfg is None or not dcfg.enabled:
        return None
    wf = PassageWorkflow(cfg, profile, dcfg.params)
    return wf if wf.enabled else None


def analyze_video(
    video_path: str,
    cfg: RulesConfig,
    adapter: PoseAdapter,
    run_id: str,
    run_dir: str,
    opts: AnalyzeOptions,
) -> tuple[VideoReport, list[Event]]:
    started = time.time()
    meta = video_reader.probe(video_path)
    profile = pick_profile(cfg, video_path, opts)
    print(f"→ {os.path.basename(video_path)}  videoId={meta.video_id}  profile={profile.id if profile else None}")

    events: list[Event] = []
    warnings: list[str] = []
    rule_hits: dict[str, int] = {}
    rule_cover: dict[str, float] = {}

    frames_iter, read_report, cache_path = _iter_observations(video_path, cfg, profile, run_id, adapter, opts, meta)
    detectors = _make_detectors(cfg, profile) if profile else []
    workflow = _make_workflow(cfg, profile) if profile else None
    for d in detectors:
        if hasattr(d, "reset"):
            d.reset()
    if workflow is not None:
        workflow.reset()

    last_t = 0.0
    n_frames = 0
    evidence_by_rule: dict[str, int] = {}
    for det in detectors:
        rule_hits.setdefault(getattr(det, "rule_id", det.__class__.__name__), 0)
    if workflow is not None:
        rule_hits.setdefault(workflow.rule_id, 0)

    for frame in frames_iter:
        n_frames += 1
        last_t = frame.media_time_sec
        for det in detectors:
            rid = getattr(det, "rule_id", det.__class__.__name__)
            res = det.update(frame)
            ev_list = res if isinstance(res, list) else ([res] if res is not None else [])
            for ev in ev_list:
                events.append(ev)
                rule_hits[rid] = rule_hits.get(rid, 0) + 1
                if opts.save_evidence:
                    _maybe_save_evidence(run_dir, ev, frame, profile, cfg, evidence_by_rule, opts)
        if workflow is not None:
            workflow.update(frame)

    # finalize
    for det in detectors:
        rid = getattr(det, "rule_id", det.__class__.__name__)
        if rid == "access.crossing":
            continue
        try:
            tail = det.finalize(last_t, run_id, meta.video_id, profile.id if profile else None)
        except TypeError:
            tail = None
        tail_list = tail if isinstance(tail, list) else ([tail] if tail is not None else [])
        for ev in tail_list:
            events.append(ev)
            rule_hits[rid] = rule_hits.get(rid, 0) + 1

    if workflow is not None:
        w_events = workflow.finalize(run_id, meta.video_id, profile.id if profile else None, last_t)
        for ev in w_events:
            events.append(ev)
            rule_hits[workflow.rule_id] = rule_hits.get(workflow.rule_id, 0) + 1
            if opts.save_evidence:
                _maybe_save_evidence(run_dir, ev, None, profile, cfg, evidence_by_rule, opts, source_path=video_path)

    # 覆盖率
    for rid, cnt in rule_hits.items():
        rule_cover[rid] = float(cnt)

    finished = time.time()
    report = VideoReport(
        video_id=meta.video_id,
        source_path=os.path.abspath(video_path),
        camera_profile_id=profile.id if profile else None,
        width=meta.width,
        height=meta.height,
        fps_nominal=meta.fps_nominal,
        frame_count=meta.frame_count,
        duration_sec=meta.duration_sec,
        sampled_frames=read_report.sampled_frames or n_frames,
        analyzable_sec=last_t,
        time_source=read_report.time_source,  # type: ignore[arg-type]
        event_count=len(events),
        rule_coverage=rule_cover,
        unknown_sec={},
        warnings=list(warnings) + list(read_report.warnings),
        started_at=started,
        finished_at=finished,
        status="ok",
    )
    reports.write_video_report(run_dir, report)
    reports.copy_source_reference(run_dir, video_path, meta.video_id)
    if cache_path:
        report.warnings.append(f"cache={os.path.basename(cache_path)}")
    return report, events


def _maybe_save_evidence(
    run_dir: str,
    event: Event,
    frame: FrameObservation | None,
    profile: CameraProfile | None,
    cfg: RulesConfig,
    evidence_by_rule: dict[str, int],
    opts: AnalyzeOptions,
    source_path: str | None = None,
) -> None:
    n = evidence_by_rule.get(event.rule_id, 0)
    if n >= opts.max_evidence_per_rule:
        return
    evidence_by_rule[event.rule_id] = n + 1
    img: np.ndarray | None = None
    overlay: dict[str, Any] = {
        "zones": [
            {
                "points": [
                    [p[0] * (frame.width if frame else 1920), p[1] * (frame.height if frame else 1080)]
                    for p in z.points
                ],
                "label": z.label or z.id,
            }
            for z in (profile.zones if profile else [])
        ],
        "lines": [
            {
                "a": [ln.a[0] * (frame.width if frame else 1920), ln.a[1] * (frame.height if frame else 1080)],
                "b": [ln.b[0] * (frame.width if frame else 1920), ln.b[1] * (frame.height if frame else 1080)],
            }
            for ln in (profile.lines if profile else [])
        ],
    }
    if frame is not None:
        # 直接从缓存观测无法拿到原图，需要按时间戳 seek
        pass
    if source_path is None and frame is not None:
        source_path = None  # 由上层通过 per-video 报告查找
    # 尝试通过 event 关联的 video 找源文件
    if source_path is None:
        # 查找 run 目录下 sources 软链
        src_dir = os.path.join(run_dir, "sources")
        if os.path.isdir(src_dir):
            for name in os.listdir(src_dir):
                if name.startswith(event.video_id):
                    source_path = os.path.join(src_dir, name)
                    break
    if source_path and os.path.exists(source_path):
        t_sec = event.trigger_sec if event.trigger_sec is not None else (event.start_sec + event.end_sec) * 0.5
        img = video_reader.decode_at(source_path, float(t_sec))
    if img is None:
        return
    rel = reports.save_evidence_frame(run_dir, event, img, phase="trigger", overlay=overlay)
    if rel:
        event.keyframes.append({"tSec": float(event.trigger_sec or event.start_sec), "path": rel, "phase": "trigger"})


# ---------------------------------------------------------------------------
# 批量入口
# ---------------------------------------------------------------------------


def run_analyze(opts: AnalyzeOptions) -> RunSummary:
    cfg = load_config(opts.config_path)
    if opts.device:
        cfg.device = opts.device
    run_id = opts.run_id or time.strftime("run-%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    run_dir = os.path.join(opts.output_dir, run_id) if not os.path.isabs(opts.output_dir) or not opts.output_dir.endswith(run_id) else opts.output_dir
    # 若 output_dir 已经是 run 目录（用户显式指定），沿用；否则拼接 run_id
    if os.path.basename(os.path.abspath(opts.output_dir)) != run_id:
        run_dir = os.path.join(opts.output_dir, run_id)
    else:
        run_dir = os.path.abspath(opts.output_dir)
        run_id = os.path.basename(run_dir)
    os.makedirs(run_dir, exist_ok=True)

    videos = collect_videos(opts.input_path)
    if not videos:
        raise SystemExit(f"No videos found at {opts.input_path}")

    model_path = resolve_model_path(cfg.model_id)
    adapter = PoseAdapter(
        model_path=model_path,
        device=cfg.device,
        conf=cfg.conf,
        iou=cfg.iou,
    )
    adapter.load()
    print(f"Run {run_id}: {len(videos)} videos, model={cfg.model_id} hash={adapter.model_hash} device={cfg.device}")

    started = time.time()
    all_events: list[Event] = []
    reports_list: list[VideoReport] = []
    for vp in videos:
        # 每个视频用全新的跟踪上下文
        adapter.close()
        adapter = PoseAdapter(model_path=model_path, device=cfg.device, conf=cfg.conf, iou=cfg.iou)
        adapter.load()
        try:
            rep, evs = analyze_video(vp, cfg, adapter, run_id, run_dir, opts)
        except Exception as e:  # noqa: BLE001
            rep = VideoReport(
                video_id=os.path.basename(vp),
                source_path=os.path.abspath(vp),
                camera_profile_id=None,
                width=0,
                height=0,
                fps_nominal=0,
                frame_count=0,
                duration_sec=0,
                sampled_frames=0,
                analyzable_sec=0,
                time_source="frame_index",
                event_count=0,
                started_at=time.time(),
                finished_at=time.time(),
                status="failed",
                error=str(e),
            )
            evs = []
            print(f"  ! failed: {e}")
        reports_list.append(rep)
        all_events.extend(evs)
        adapter.close()

    finished = time.time()
    totals: dict[str, Any] = {
        "videos": len(reports_list),
        "events": len(all_events),
        "byRule": {},
        "durationSec": finished - started,
    }
    for ev in all_events:
        totals["byRule"][ev.rule_id] = totals["byRule"].get(ev.rule_id, 0) + 1

    summary = RunSummary(
        run_id=run_id,
        started_at=started,
        finished_at=finished,
        config_path=os.path.abspath(opts.config_path),
        config_hash=_config_hash(opts.config_path),
        model_id=cfg.model_id,
        model_hash=adapter.model_hash,
        device=cfg.device,
        videos=reports_list,
        events=all_events,
        totals=totals,
    )
    reports.write_run_json(run_dir, summary)
    reports.write_events_json(run_dir, all_events)
    reports.write_events_csv(run_dir, all_events)
    print(f"✓ run_dir={run_dir}  events={len(all_events)}  byRule={totals['byRule']}")
    return summary
