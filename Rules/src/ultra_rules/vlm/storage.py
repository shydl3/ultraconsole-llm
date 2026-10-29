from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

TERMINAL = {"completed", "partial", "failed", "cancelled", "interrupted"}
VERSION = "vlm-1"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def append_json(path: Path, data):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, allow_nan=False) + "\n")
        f.flush()


def iter_jsonl(path: Path):
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # A cancelled writer can leave an incomplete final record.
                    continue


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def file_hash(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def progress(run_dir: Path, **values):
    path = run_dir / "progress.json"
    current = read_json(path) if path.exists() else {}
    write_json(path, {**current, **values, "updatedAt": time.time()})
