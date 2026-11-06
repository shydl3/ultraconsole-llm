#!/usr/bin/env python3
"""Download Qwen3-VL weights from mainland-reachable sources.

Default: ModelScope (no redirect to overseas CDNs), fallback: hf-mirror.
YOLO11 pose weights are shipped inside the Windows delivery package; they are
never downloaded here. If missing, setup fails with an explicit hint.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

MODELS = Path(os.environ.get("MODELS_DIR", "/models"))
DETECT = MODELS / "detect"
YOLO_NAME = "yolo11m-pose.pt"
# 容器用 /models/detect；WSL 开发用 backend/models（bootstrap 传入）。
YOLO_PATH = Path(os.environ.get("YOLO_PATH") or (DETECT / YOLO_NAME))

QWEN_REPO = os.environ.get("VLM_MODEL_REPO", "Qwen/Qwen3-VL-2B-Instruct")
QWEN_TARGET = Path(os.environ.get("VLM_MODEL_PATH") or MODELS / QWEN_REPO.rsplit("/", 1)[-1])

MODEL_SOURCE = os.environ.get("VLM_MODEL_SOURCE", "modelscope").strip().lower()
MS_ENDPOINT = os.environ.get("MODELSCOPE_ENDPOINT", "https://modelscope.cn").rstrip("/")
MS_REVISION = os.environ.get("MODELSCOPE_REVISION", "master")
HF_ENDPOINT = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com").rstrip("/")


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


def _urlopen(url: str, timeout: int = 120):
    request = urllib.request.Request(url, headers={"User-Agent": "ultraconsole-setup"})
    return urllib.request.urlopen(request, timeout=timeout)


def _download(url: str, target: Path, expected_size: int | None = None) -> str:
    if target.is_file() and target.stat().st_size > 0:
        if expected_size is None or target.stat().st_size == expected_size:
            return "已存在"

    tmp = target.with_name(target.name + ".part")
    with _urlopen(url) as source, tmp.open("wb") as sink:
        done = 0
        next_report = 256 * 1024 * 1024
        while chunk := source.read(1024 * 1024):
            sink.write(chunk)
            done += len(chunk)
            if done >= next_report:
                print(f"[models]   {target.name}: {done / 1e9:.2f} GB", flush=True)
                next_report += 256 * 1024 * 1024

    if expected_size is not None and tmp.stat().st_size != expected_size:
        actual = tmp.stat().st_size
        tmp.unlink(missing_ok=True)
        raise OSError(f"大小不符（期望 {expected_size}，实际 {actual}）")

    tmp.replace(target)
    return "下载完成"


def _modelscope_files() -> list[dict]:
    query = urllib.parse.urlencode({"Revision": MS_REVISION, "Recursive": "True"})
    url = f"{MS_ENDPOINT}/api/v1/models/{QWEN_REPO}/repo/files?{query}"
    with _urlopen(url, 60) as response:
        payload = json.load(response)
    if not payload.get("Success"):
        raise RuntimeError(payload.get("Message") or "ModelScope 返回失败")
    files = (payload.get("Data") or {}).get("Files") or []
    blobs = [item for item in files if item.get("Type") == "blob" and item.get("Path")]
    if not blobs:
        raise RuntimeError("ModelScope 文件列表为空")
    return blobs


def download_modelscope() -> None:
    blobs = _modelscope_files()
    total = sum(int(item.get("Size") or 0) for item in blobs)
    print(f"[models] ModelScope: {len(blobs)} 个文件，共 {total / 1e9:.2f} GB", flush=True)

    def fetch(item: dict) -> str:
        relative = str(item["Path"])
        target = QWEN_TARGET / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        quoted = urllib.parse.quote(relative, safe="/")
        url = f"{MS_ENDPOINT}/models/{QWEN_REPO}/resolve/{MS_REVISION}/{quoted}"
        size = int(item["Size"]) if item.get("Size") else None
        return _download(url, target, size)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch, item): item for item in blobs}
        for future in as_completed(futures):
            name = futures[future]["Path"]
            try:
                state = future.result()
            except Exception as exc:  # noqa: BLE001 - 汇总后统一报错
                raise RuntimeError(f"{name}: {exc}") from exc
            print(f"[models] {name}: {state}", flush=True)


def download_hf() -> None:
    os.environ["HF_ENDPOINT"] = HF_ENDPOINT
    from huggingface_hub import snapshot_download

    print(f"[models] HuggingFace 镜像 {HF_ENDPOINT}：{QWEN_REPO}", flush=True)
    snapshot_download(repo_id=QWEN_REPO, local_dir=str(QWEN_TARGET), max_workers=4)


def ensure_yolo() -> None:
    target = YOLO_PATH
    if target.is_file() and target.stat().st_size > 1_000_000:
        print(f"[models] 已存在：{target}", flush=True)
        return
    raise SystemExit(
        f"[models] 缺少 {target}。\n"
        "[models] Windows 交付包已内置该文件；其他部署方式请手动把 "
        f"{YOLO_NAME} 放到 {target.parent} 后重试。"
    )


def main() -> None:
    DETECT.mkdir(parents=True, exist_ok=True)

    if qwen_ready(QWEN_TARGET):
        print(f"[models] 已存在：{QWEN_TARGET}", flush=True)
    else:
        errors: list[str] = []
        if MODEL_SOURCE in {"modelscope", "auto", ""}:
            try:
                download_modelscope()
            except Exception as exc:  # noqa: BLE001 - 允许切到备用源
                errors.append(f"ModelScope: {exc}")
                print(f"[models] ModelScope 下载失败，尝试备用源：{exc}", file=sys.stderr, flush=True)
        if not qwen_ready(QWEN_TARGET) and MODEL_SOURCE in {"huggingface", "auto", "modelscope", ""}:
            try:
                download_hf()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"HuggingFace: {exc}")
                print(f"[models] HuggingFace 下载失败：{exc}", file=sys.stderr, flush=True)
        if not qwen_ready(QWEN_TARGET):
            raise SystemExit("[models] Qwen 权重不完整：" + "；".join(errors) if errors else "[models] Qwen 权重不完整")

    ensure_yolo()


if __name__ == "__main__":
    main()
