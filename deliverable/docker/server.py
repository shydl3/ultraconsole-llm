"""Serve the API and the built SPA from a single origin inside the container.

The Vue/React frontend uses relative `/api` and `/ws` URLs, so mounting the
compiled bundle at `/` keeps cookies, WebSocket and media Range requests on the
same origin as the API. API routes are registered before this mount, so they
take precedence.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi.staticfiles import StaticFiles

from app.main import app

DIST = Path(__file__).resolve().parent / "frontend" / "dist"

if DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="ui")
else:
    print(f"[ultraconsole] 警告：未找到前端构建产物 {DIST}，仅提供 API。", file=sys.stderr)
