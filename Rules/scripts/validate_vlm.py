"""Run the seven local sample videos serially and record engineering outcomes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    output = ROOT / "Rules/data/vlm"
    validation = output / "validation" / time.strftime("%Y%m%d-%H%M%S")
    validation.mkdir(parents=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "Rules/src") + os.pathsep + env.get("PYTHONPATH", "")
    summary = []
    for video in sorted((ROOT / "安检监控视频-测试").glob("*.mp4")):
        profile = "c2" if video.name.startswith("20260608") else "d2"
        cmd = [sys.executable, "-m", "ultra_rules.vlm.cli", "analyze", "--input", str(video),
               "--config", str(ROOT / f"Rules/configs/vlm_{profile}.json"), "--output", str(output)]
        started = time.perf_counter()
        result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=3600)
        (validation / f"{video.stem}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        paths = [line.removeprefix("run_dir=") for line in result.stdout.splitlines() if line.startswith("run_dir=")]
        entry = {"video": video.name, "exitCode": result.returncode, "elapsedSeconds": time.perf_counter() - started}
        if paths:
            run_dir = Path(paths[0])
            entry["runId"] = run_dir.name
            for name in ("status", "progress", "summary", "runtime"):
                if (run_dir / f"{name}.json").exists():
                    entry[name] = json.loads((run_dir / f"{name}.json").read_text())
        summary.append(entry)
        (validation / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(entry, ensure_ascii=False), flush=True)
    print(f"Validation saved: {validation}", flush=True)
    if any(row["exitCode"] for row in summary):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
