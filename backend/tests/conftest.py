import json

import pytest


@pytest.fixture
def local_vlm_models(tmp_path, monkeypatch):
    # Metadata fixtures only: tests must not load actual model weights or CUDA.
    from app import vlm_jobs  # noqa: F401
    from ultra_rules.vlm import models

    root = tmp_path / "models"
    paths = []
    for size in ("2B", "4B"):
        path = root / f"Qwen3-VL-{size}-Instruct"
        path.mkdir(parents=True)
        (path / "config.json").write_text(json.dumps({"model_type": "qwen3_vl"}))
        for name in ("preprocessor_config.json", "tokenizer_config.json", "tokenizer.json"):
            (path / name).write_text('{}')
        (path / "model.safetensors").write_bytes(b'fixture')
        paths.append(path)
    monkeypatch.setattr(models, "MODEL_ROOT", root)
    monkeypatch.setenv("VLM_MODEL_PATH", str(paths[0]))
    return paths
