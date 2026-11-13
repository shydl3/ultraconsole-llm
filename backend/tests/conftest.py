import json

import pytest


@pytest.fixture(autouse=True)
def _disable_startup_autostart(monkeypatch):
    """Startup auto-selection must never load real weights or CUDA during tests."""
    from dataclasses import replace

    import app.main as main

    monkeypatch.setattr(main, "settings", replace(main.settings, autostart=False))


@pytest.fixture(autouse=True)
def _isolate_vlm_data_root(tmp_path, monkeypatch):
    """The queue scheduler runs during the app lifespan; keep it off real run data."""
    from app import vlm_api

    monkeypatch.setattr(vlm_api, "DATA_ROOT", tmp_path / "vlm-data")


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
