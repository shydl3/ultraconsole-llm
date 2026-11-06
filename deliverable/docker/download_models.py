#!/usr/bin/env python3
"""Download model weights into the mounted /models volume (idempotent)."""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path

MODELS = Path(os.environ.get("MODELS_DIR", "/models"))
DETECT = MODELS / "detect"

QWEN_REPO = os.environ.get("VLM_MODEL_REPO", "Qwen/Qwen3-VL-2B-Instruct")
QWEN_TARGET = Path(
    os.environ.get("VLM_MODEL_PATH") or MODELS / QWEN_REPO.rsplit("/", 1)[-1]
)

YOLO_FILES = {
    # 人员姿态跟踪，VLM 分析必需。
    "yolo11m-pose.pt": (
        "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11m-pose.pt",
        True,
    ),
    # 经典 CV 控制台的默认检测模型，可选。
    "yolov8n.pt": (
        "https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8n.pt",
        False,
    ),
}


def qwen_ready(path: Path) -> bool:
    if not (path / "config.json").is_file():
        return False
    index = path / "model.safetensors.index.json"
    if index.is_file():
        try:
            mapping = json.loads(index.read_text())["weight_map"]
        except (OSError, ValueError, KeyError, TypeError):
            return False
        return all((path / name).is_file() for name in set(mapping.values()))
    return (path / "model.safetensors").is_file()


def download_qwen() -> None:
    if qwen_ready(QWEN_TARGET):
        print(f"[models] 已存在：{QWEN_TARGET}", flush=True)
        return
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise SystemExit("[models] 缺少 huggingface_hub，无法下载 Qwen 权重") from None

    print(f"[models] 下载 {QWEN_REPO} -> {QWEN_TARGET}", flush=True)
    QWEN_TARGET.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=QWEN_REPO, local_dir=str(QWEN_TARGET), max_workers=4)
    if not qwen_ready(QWEN_TARGET):
        raise SystemExit(f"[models] 下载完成但权重不完整：{QWEN_TARGET}")


def download_file(name: str, url: str) -> None:
    target = DETECT / name
    if target.is_file() and target.stat().st_size > 1_000_000:
        print(f"[models] 已存在：{target}", flush=True)
        return

    tmp = target.with_name(target.name + ".part")
    for attempt in range(1, 4):
        try:
            print(f"[models] 下载 {name}（第 {attempt} 次）", flush=True)
            request = urllib.request.Request(url, headers={"User-Agent": "ultraconsole-setup"})
            with urllib.request.urlopen(request, timeout=120) as source, tmp.open("wb") as sink:
                shutil.copyfileobj(source, sink, length=1024 * 1024)
            if tmp.stat().st_size < 1_000_000:
                raise OSError("下载文件过小")
            tmp.replace(target)
            return
        except Exception as exc:  # noqa: BLE001 - 网络错误需要重试并汇报
            print(f"[models] 下载失败：{exc}", file=sys.stderr, flush=True)
            tmp.unlink(missing_ok=True)
            if attempt < 3:
                time.sleep(3)
    raise SystemExit(f"[models] 无法下载 {name}，可手动放到 {DETECT / name}")


def main() -> None:
    DETECT.mkdir(parents=True, exist_ok=True)
    download_qwen()
    for name, (url, required) in YOLO_FILES.items():
        try:
            download_file(name, url)
        except SystemExit:
            if required:
                raise
            print(f"[models] 可选模型 {name} 下载失败，已跳过", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
