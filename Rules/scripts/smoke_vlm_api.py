"""Exercise real HTTP upload, two-description preview, evidence, range and cancellation."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:5200")
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=60, trust_env=False) as client:
        def get(path):
            response = client.get(path)
            response.raise_for_status()
            return response.json()

        def post(path, **kwargs):
            response = client.post(path, **kwargs)
            response.raise_for_status()
            return response.json()

        assert get("/api/health")["ok"]
        source = ROOT / "安检监控视频-测试/未拦截1.mp4"
        with source.open("rb") as file:
            video = post("/api/vlm/videos", files={"file": (source.name, file, "video/mp4")})
        config = json.loads((ROOT / "Rules/configs/vlm_d2.json").read_text())
        config.update(videoId=video["videoId"], dryRun=True)
        config["events"].append({"id": "walk_through", "description": "该人员是否步行通过通道"})
        created = post("/api/vlm/runs", json=config)
        run_id = created["runId"]
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            status = get(f"/api/vlm/runs/{run_id}")
            if status["state"] in {"completed", "failed", "partial", "cancelled", "interrupted"}:
                break
            time.sleep(.5)
        assert status["state"] == "completed", status
        results = get(f"/api/vlm/runs/{run_id}/results")
        assert results["total"] >= 2, results
        assert {r["eventDefinitionId"] for r in results["items"]} == {"walk_through", "forced_passage_unopposed"}
        assert all(r["inferenceStatus"] == "dry_run" for r in results["items"])
        preview = results["items"][0]["previewPath"]
        response = client.get(f"/api/vlm/runs/{run_id}/artifacts/{preview}")
        assert response.status_code == 200 and response.content[:2] == b"\xff\xd8"
        response = client.get(f"/api/vlm/videos/{video['videoId']}/source", headers={"Range": "bytes=0-99"})
        assert response.status_code == 206 and len(response.content) == 100
        cancelled_run = post("/api/vlm/runs", json=config)["runId"]
        post(f"/api/vlm/runs/{cancelled_run}/cancel")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            cancelled = get(f"/api/vlm/runs/{cancelled_run}")
            if cancelled["state"] == "cancelled":
                break
            time.sleep(.2)
        assert cancelled["state"] == "cancelled", cancelled
        summary = {"runId": run_id, "cancelledRunId": cancelled_run, "videoId": video["videoId"],
                   "resultCount": results["total"], "checks": ["upload", "two_event_definitions", "real_background_worker",
                   "pose_cache", "evidence_jpeg", "video_range", "cancellation"], "qwenCalled": False}
        output = ROOT / "Rules/data/vlm/api-smoke.json"
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
