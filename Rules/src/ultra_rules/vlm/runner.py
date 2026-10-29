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


def run(run_dir: Path):
    config = read_json(run_dir / "request.json")
    lock_path = Path(config["cacheRoot"]).parent / "worker.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    child = None
    cancelled = False
    started = time.time()

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
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for phase, python, timeout in (("tracking", config["posePython"], 86400),
                                           ("understanding", config["qwenPython"], 86400)):
                if cancelled or (run_dir / "cancel.requested").exists():
                    cancelled = True
                    break
                status(phase)
                env = dict(os.environ, HF_HUB_OFFLINE="1", TOKENIZERS_PARALLELISM="false", PYTHONUNBUFFERED="1")
                with (run_dir / f"{phase}.log").open("a") as log:
                    child = subprocess.Popen([python, "-m", "ultra_rules.vlm.cli", phase, "--run-dir", str(run_dir)],
                                             stdout=log, stderr=subprocess.STDOUT, env=env)
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
