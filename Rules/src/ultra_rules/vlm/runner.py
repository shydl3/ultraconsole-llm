from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .storage import read_json, write_json


def process_identity(pid: int):
    try:
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return None if stat[0] == "Z" else stat[19]
    except (OSError, IndexError):
        return None


def acquire_worker_lock(lock, timeout: float = 120.0):
    """Wait for the shared GPU worker slot instead of failing on a brief overlap.

    The backend queue starts the next run as soon as the previous status is
    terminal, which can be a few milliseconds before this process releases its
    lock; retrying avoids a spurious "failed" run.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def count_candidates(run_dir: Path) -> int:
    path = run_dir / "candidates.jsonl"
    if not path.exists():
        return 0
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def phase_timeout(phase: str, config: dict, run_dir: Path) -> float:
    """Safety-net timeout per phase, scaled for multi-hour surveillance files.

    A 12-24h recording spends hours in pose tracking, and the understanding phase
    makes one Qwen call per candidate; the old fixed 1h / 1day limits would kill
    legitimate long runs. ``understanding`` is evaluated after tracking finished,
    so the candidate file already exists when it is counted.
    """
    duration = float(config["video"]["durationSec"])
    if phase == "segment":
        return max(3600.0, duration)
    if phase == "tracking":
        return max(86400.0, duration * 4)
    if phase == "cues":
        return max(86400.0, duration)
    return max(86400.0, duration * 4, count_candidates(run_dir) * 30 + 3600)


def run(run_dir: Path):
    config = read_json(run_dir / "request.json")
    lock_path = Path(config["cacheRoot"]).parent / "worker.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    child = None
    cancelled = False
    # Preserve the timer set when the run was queued (in prepare), so the
    # measured duration starts from "开始分析" rather than worker startup.
    started = read_json(run_dir / "status.json").get("startedAt") or time.time()

    def signal_cancel(_signum, _frame):
        nonlocal cancelled
        cancelled = True

    signal.signal(signal.SIGTERM, signal_cancel)
    signal.signal(signal.SIGINT, signal_cancel)

    def status(state, **extra):
        write_json(run_dir / "status.json", {"runId": run_dir.name, "videoId": config["request"]["videoId"],
                                            "fileName": config["video"]["fileName"], "state": state,
                                            "startedAt": started, "updatedAt": time.time(), **extra})

    write_json(run_dir / "owner.json", {"pid": os.getpid(), "identity": process_identity(os.getpid())})
    with lock_path.open("w") as lock:
        try:
            acquire_worker_lock(lock)
            # segment runs in the pose env (only needs av); tracking/cues stay on
            # the full timeline; understanding loops segments inside one worker
            # so the Qwen model is loaded once but processed in bounded slices.
            phases = [("segment", config["posePython"]), ("tracking", config["posePython"])]
            if config["request"].get("cues"):
                phases.append(("cues", config["posePython"]))
            phases.append(("understanding", config["qwenPython"]))
            for phase, python in phases:
                if cancelled or (run_dir / "cancel.requested").exists():
                    cancelled = True
                    break
                status(phase)
                env = dict(os.environ, HF_HUB_OFFLINE="1", TOKENIZERS_PARALLELISM="false", PYTHONUNBUFFERED="1")
                with (run_dir / f"{phase}.log").open("a") as log:
                    child = subprocess.Popen([python, "-m", "ultra_rules.vlm.cli", phase, "--run-dir", str(run_dir)],
                                             stdout=log, stderr=subprocess.STDOUT, env=env)
                    timeout = phase_timeout(phase, config, run_dir)
                    deadline = time.monotonic() + timeout
                    while child.poll() is None:
                        if cancelled or (run_dir / "cancel.requested").exists():
                            cancelled = True
                            child.terminate()
                            try:
                                child.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                child.kill()
                                child.wait()
                            break
                        if time.monotonic() > deadline:
                            raise TimeoutError(f"{phase} exceeded {timeout}s")
                        time.sleep(0.3)
                    if cancelled:
                        break
                    if child.returncode:
                        tail = (run_dir / f"{phase}.log").read_text(errors="replace")[-3000:]
                        raise RuntimeError(f"{phase} exited {child.returncode}: {tail}")
                    child = None
            if cancelled:
                status("cancelled", finishedAt=time.time())
            else:
                summary = read_json(run_dir / "summary.json")
                state = "partial" if summary["failedRequests"] else "completed"
                if summary["failedRequests"] and summary["failedRequests"] == summary["resultCount"]:
                    state = "failed"
                status(state, finishedAt=time.time(), summary=summary)
        except Exception as exc:
            status("failed", finishedAt=time.time(), error=f"{type(exc).__name__}: {exc}")
        finally:
            if child and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


def main():
    run(Path(sys.argv[1]).resolve())


if __name__ == "__main__":
    main()
