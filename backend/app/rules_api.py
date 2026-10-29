"""离线规则（Rules/）运行结果的只读 + 触发接口。

数据源为 `Rules/data/runs/<runId>/` 下的 JSON/CSV/截图产物，不引入数据库。
路由前缀 `/api/rules`，自动被现有 `/api` 鉴权中间件与 Vite 代理覆盖。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RULES_ROOT = _REPO_ROOT / "Rules"
RUNS_ROOT = RULES_ROOT / "data" / "runs"
CACHE_ROOT = RULES_ROOT / "data" / "cache"
CONFIG_DEFAULT = RULES_ROOT / "configs" / "checkpoint.json"
VIDEO_INPUT_DEFAULT = _REPO_ROOT / "安检监控视频-测试"

router = APIRouter(prefix="/api/rules", tags=["rules"])

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,80}$")
_REL_PATH_RE = re.compile(r"^[A-Za-z0-9_.\-/]{1,200}$")


def _validate_run_id(run_id: str) -> str:
    if not _RUN_ID_RE.match(run_id or ""):
        raise HTTPException(status_code=400, detail="Invalid runId")
    return run_id


def _run_dir(run_id: str) -> Path:
    rid = _validate_run_id(run_id)
    d = (RUNS_ROOT / rid).resolve()
    if not str(d).startswith(str(RUNS_ROOT.resolve())):
        raise HTTPException(status_code=400, detail="Invalid runId")
    return d


def _load_json(path: Path) -> Any:
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Not found: {path.name}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 运行列表 / 详情
# ---------------------------------------------------------------------------


@router.get("/runs")
def list_runs(limit: int = Query(default=50, ge=1, le=200)) -> list[dict[str, Any]]:
    if not RUNS_ROOT.is_dir():
        return []
    out: list[dict[str, Any]] = []
    entries = [p for p in RUNS_ROOT.iterdir() if p.is_dir() and (p / "run.json").is_file()]
    parsed: list[tuple[float, str, dict[str, Any]]] = []
    for d in entries:
        try:
            data = _load_json(d / "run.json")
        except Exception:  # noqa: BLE001
            continue
        started = data.get("startedAt") or 0
        try:
            started_f = float(started)
        except (TypeError, ValueError):
            started_f = 0.0
        parsed.append(
            (
                started_f,
                d.name,
                {
                    "runId": data.get("runId"),
                    "startedAt": data.get("startedAt"),
                    "finishedAt": data.get("finishedAt"),
                    "modelId": data.get("modelId"),
                    "device": data.get("device"),
                    "configHash": data.get("configHash"),
                    "totals": data.get("totals", {}),
                    "videoCount": len(data.get("videos", [])),
                    "eventCount": len(data.get("events", [])),
                },
            )
        )
    # 最近运行优先：按 startedAt 倒序，缺失时间戳的按名称兜底
    parsed.sort(key=lambda x: (x[0], x[1]), reverse=True)
    for _, _, item in parsed[:limit]:
        out.append(item)
    return out


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    d = _run_dir(run_id)
    return _load_json(d / "run.json")


@router.get("/runs/{run_id}/events")
def get_events(
    run_id: str,
    videoId: str | None = Query(default=None),
    ruleId: str | None = Query(default=None),
    review: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    d = _run_dir(run_id)
    events = _load_json(d / "events.json")
    out = []
    for e in events:
        if videoId and e.get("videoId") != videoId:
            continue
        if ruleId and e.get("ruleId") != ruleId:
            continue
        if review and e.get("review") != review:
            continue
        out.append(e)
    return out


@router.get("/runs/{run_id}/videos")
def get_videos(run_id: str) -> list[dict[str, Any]]:
    d = _run_dir(run_id)
    data = _load_json(d / "run.json")
    return data.get("videos", [])


# ---------------------------------------------------------------------------
# 证据截图 / 源视频流
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}/frames/{rel_path:path}")
def get_frame(run_id: str, rel_path: str) -> Response:
    d = _run_dir(run_id)
    if not _REL_PATH_RE.match(rel_path or ""):
        raise HTTPException(status_code=400, detail="Invalid path")
    target = (d / rel_path).resolve()
    if not str(target).startswith(str(d.resolve())):
        raise HTTPException(status_code=400, detail="Path escapes run dir")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Frame not found")
    return FileResponse(target, media_type="image/jpeg")


@router.get("/runs/{run_id}/videos/{video_id}/source")
def get_source(run_id: str, video_id: str) -> Response:
    """以软链/拷贝方式提供源视频，支持 HTTP Range 以便 <video> 拖动。"""
    d = _run_dir(run_id)
    if not re.match(r"^[A-Za-z0-9_.\-]{1,64}$", video_id or ""):
        raise HTTPException(status_code=400, detail="Invalid videoId")
    src_dir = d / "sources"
    if not src_dir.is_dir():
        raise HTTPException(status_code=404, detail="No sources")
    candidates = [p for p in src_dir.iterdir() if p.name.startswith(video_id)]
    if not candidates:
        raise HTTPException(status_code=404, detail="Source not found")
    target = candidates[0].resolve()
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Source not a file")
    return FileResponse(target, media_type="video/mp4", filename=target.name)


# ---------------------------------------------------------------------------
# 触发新分析（后台子进程，不阻塞 web 服务）
# ---------------------------------------------------------------------------

_active_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


@router.post("/runs/start")
def start_run(
    input_path: str | None = Query(default=None),
    config_path: str | None = Query(default=None),
    device: str | None = Query(default=None),
) -> dict[str, Any]:
    """以子进程方式启动一次 CLI 分析，立即返回 jobId。"""
    cfg = Path(config_path) if config_path else CONFIG_DEFAULT
    inp = Path(input_path) if input_path else VIDEO_INPUT_DEFAULT
    if not cfg.is_file():
        raise HTTPException(status_code=400, detail=f"Config not found: {cfg}")
    if not inp.exists():
        raise HTTPException(status_code=400, detail=f"Input not found: {inp}")

    run_id = time.strftime("web-%Y%m%d-%H%M%S")
    py = sys.executable
    cmd = [
        py,
        "-m",
        "ultra_rules.cli",
        "analyze",
        "--input",
        str(inp),
        "--config",
        str(cfg),
        "--output",
        str(RUNS_ROOT),
        "--cache-dir",
        str(CACHE_ROOT),
        "--run-id",
        run_id,
        "--profile-map",
        "未拦截=d2,正常拦截=d2,20260608=c2",
    ]
    if device:
        cmd += ["--device", device]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(RULES_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    log_path = RUNS_ROOT / f"{run_id}.log"
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    logf = open(log_path, "w", encoding="utf-8")  # noqa: SIM115
    proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, env=env, cwd=str(_REPO_ROOT))
    with _jobs_lock:
        _active_jobs[run_id] = {"pid": proc.pid, "startedAt": time.time(), "log": str(log_path)}
    return {"ok": True, "runId": run_id, "pid": proc.pid, "log": str(log_path)}


@router.get("/runs/{run_id}/status")
def run_status(run_id: str) -> dict[str, Any]:
    rid = _validate_run_id(run_id)
    with _jobs_lock:
        job = _active_jobs.get(rid)
    d = _run_dir(rid)
    done = (d / "run.json").is_file()
    log_path = d.with_suffix(".log") if not done else None
    tail = ""
    lp = (job or {}).get("log") or (str(log_path) if log_path else None)
    if lp and os.path.isfile(lp):
        try:
            with open(lp, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            tail = "".join(lines[-30:])
        except OSError:
            tail = ""
    return {"runId": rid, "running": bool(job) and not done, "done": done, "logTail": tail}
