from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

RULES_SRC = Path(__file__).resolve().parents[2] / "Rules/src"
if str(RULES_SRC) not in sys.path:
    sys.path.insert(0, str(RULES_SRC))

from ultra_rules.vlm.cli import prepare as prepare  # noqa: E402
from ultra_rules.vlm.contracts import AnalysisParameters as AnalysisParameters  # noqa: E402
from ultra_rules.vlm.contracts import EventDefinition as EventDefinition  # noqa: E402
from ultra_rules.vlm.contracts import Identifier as Identifier  # noqa: E402
from ultra_rules.vlm.contracts import Poi as Poi  # noqa: E402
from ultra_rules.vlm.contracts import RunRequest as RunRequest  # noqa: E402
from ultra_rules.vlm.contracts import StrictModel as StrictModel  # noqa: E402
from ultra_rules.vlm.runner import process_identity  # noqa: E402
from ultra_rules.vlm.storage import TERMINAL as TERMINAL  # noqa: E402
from ultra_rules.vlm.storage import iter_jsonl as iter_jsonl  # noqa: E402
from ultra_rules.vlm.storage import read_json as read_json  # noqa: E402
from ultra_rules.vlm.storage import write_json as write_json  # noqa: E402

launch_lock = threading.Lock()


def status(run_dir: Path):
    result = read_json(run_dir / "status.json")
    owner = read_json(run_dir / "owner.json") if (run_dir / "owner.json").exists() else None
    if result["state"] not in TERMINAL and owner and process_identity(owner["pid"]) != owner["identity"]:
        if (run_dir / "cancel.requested").exists():
            result.update(state="cancelled", finishedAt=time.time(), updatedAt=time.time())
        else:
            result.update(state="interrupted", error="Worker exited without a terminal status", updatedAt=time.time())
        write_json(run_dir / "status.json", result)
    if result["state"] not in TERMINAL and not owner and time.time() - (run_dir / "status.json").stat().st_mtime > 30:
        result.update(state="interrupted", error="Worker never started")
        write_json(run_dir / "status.json", result)
    if (run_dir / "progress.json").exists():
        result["progress"] = read_json(run_dir / "progress.json")
    if (run_dir / "cancel.requested").exists() and result["state"] not in TERMINAL:
        result["state"] = "cancelling"
    return result


def launch(run_dir: Path, python: str):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(RULES_SRC) + os.pathsep + env.get("PYTHONPATH", "")
    with (run_dir / "coordinator.log").open("w") as log:
        proc = subprocess.Popen([python, "-m", "ultra_rules.vlm.runner", str(run_dir)],
                                stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
    write_json(run_dir / "owner.json", {"pid": proc.pid, "identity": process_identity(proc.pid)})

    def reap():
        proc.wait()
        status(run_dir)
    threading.Thread(target=reap, daemon=True).start()


def cancel(run_dir: Path):
    current = status(run_dir)
    if current["state"] in TERMINAL:
        return current
    (run_dir / "cancel.requested").touch()
    owner = read_json(run_dir / "owner.json") if (run_dir / "owner.json").exists() else None
    if current["state"] != "queued" and owner and process_identity(owner["pid"]) == owner["identity"]:
        # Coordinator handles TERM, terminates and reaps its active phase process.
        try:
            os.kill(owner["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
    return status(run_dir)
