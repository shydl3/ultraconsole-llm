"""Local Qwen3-VL discovery and file checks; never loads weights or touches CUDA."""
from __future__ import annotations

import json
import os
from pathlib import Path

MODEL_ROOT = Path(__file__).resolve().parents[5]


def default_model_path() -> str:
    return str(Path(os.environ.get("VLM_MODEL_PATH") or MODEL_ROOT / "Qwen3-VL-2B-Instruct").expanduser().resolve())


def validate_model_path(value: str) -> str:
    path = Path(value).expanduser()
    if not value.strip() or not path.is_absolute():
        raise ValueError("模型权重路径必须是本机绝对路径")
    path = path.resolve()
    try:
        config = json.loads((path / "config.json").read_text())
        if not isinstance(config, dict) or config.get("model_type") != "qwen3_vl":
            raise ValueError("当前加载器仅支持 Qwen3-VL 模型")
        for name in ("preprocessor_config.json", "tokenizer_config.json", "tokenizer.json"):
            if not (path / name).is_file() or (path / name).stat().st_size == 0:
                raise ValueError(f"模型文件缺失或为空：{name}")
        index = path / "model.safetensors.index.json"
        if index.exists():
            manifest = json.loads(index.read_text())
            mapping = manifest.get("weight_map") if isinstance(manifest, dict) else None
            if not isinstance(mapping, dict) or not mapping:
                raise ValueError("模型权重索引缺少 weight_map")
            names = set(mapping.values())
        else:
            names = {"model.safetensors"}
        for name in names:
            if not isinstance(name, str) or Path(name).name != name:
                raise ValueError("模型权重索引包含无效文件名")
            weight = path / name
            if not weight.is_file() or weight.stat().st_size == 0:
                raise ValueError(f"模型权重文件缺失或为空：{name}")
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"模型不可用（{path}）：{exc}") from exc
    return str(path)


def available_models() -> list[dict]:
    default = default_model_path()
    paths = {default, *(str(path.resolve()) for path in MODEL_ROOT.glob("Qwen3-VL-*") if path.is_dir())}
    result = []
    for path in sorted(paths, key=lambda value: (value != default, value)):
        error = None
        try:
            validate_model_path(path)
        except ValueError as exc:
            error = str(exc)
        result.append({"path": path, "name": Path(path).name, "available": error is None, "error": error})
    return result
