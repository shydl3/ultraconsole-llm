from __future__ import annotations

import asyncio
import base64
import io
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from PIL import Image

from .config import settings
from .devices import ensure_device_available
from .infer_runtime import UltralyticsRuntime
from .library_api import router as library_router
from .schemas import (
    ModelInfo,
    Params,
    PredResponse,
    SelectEngineRequest,
    SelectModelRequest,
    SystemStats,
    UpdateParamsRequest,
)
from .security import require_http_auth, require_ws_auth
from .state import AppState
from .system_stats import system_stats
from .video_api import router as video_router
from .vlm_api import router as vlm_router
from .webhook import NotifyRequest, NotifyResult, WebhookConfig
from .webhook import manager as webhook_manager

_DEFAULT_MODELS_DIR = str(Path(__file__).resolve().parent.parent / "models")
MODELS_DIR = os.environ.get("ULTRA_MODELS_DIR", _DEFAULT_MODELS_DIR)
_MODELS_DIR_REAL = os.path.realpath(MODELS_DIR)
_VALID_MODEL_EXTS = (".pt", ".onnx", ".engine")


def _validated_model_id(model_id: str) -> str:
    """防路径穿越：只允许 models 目录直接子文件，且后缀合法。"""
    fn = os.path.basename(model_id or "")
    if not fn or fn != model_id:
        raise ValueError(f"Invalid modelId: {model_id!r}")
    if not fn.endswith(_VALID_MODEL_EXTS):
        raise ValueError(f"Unsupported model extension: {fn}")
    full = os.path.realpath(os.path.join(_MODELS_DIR_REAL, fn))
    if os.path.dirname(full) != _MODELS_DIR_REAL:
        raise ValueError(f"Model path escapes models dir: {model_id!r}")
    return fn

app = FastAPI(title="Ultralytics Infer Console API", version="0.1.0")
state = AppState()
runtime = UltralyticsRuntime(models_dir=MODELS_DIR, max_tracker_contexts=settings.max_tracker_contexts)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=bool(settings.api_token),
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(vlm_router)
app.include_router(video_router)
app.include_router(library_router)


@app.middleware("http")
async def api_auth_middleware(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.url.path != "/api/health" and request.method != "OPTIONS":
        try:
            require_http_auth(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


def _device_str(device: str) -> str:
    return "cuda" if device == "cuda" else "cpu"


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "ts": time.time()}


@app.get("/api/models", response_model=list[ModelInfo])
def list_models() -> list[ModelInfo]:
    items = runtime.list_models()
    out: list[ModelInfo] = []
    for model_id, _path in items:
        out.append(
            ModelInfo(
                id=model_id,
                filename=model_id,
                taskType="detect",
                names={},
            )
        )

    # 如果当前已加载，则回填真实信息（taskType / names）
    cur = runtime.current()
    if cur is not None:
        for i, m in enumerate(out):
            if m.id == cur.id:
                out[i] = ModelInfo(id=cur.id, filename=cur.filename, taskType=cur.task_type, names=cur.names)
                break
    return out


@app.post("/api/models/select", response_model=ModelInfo)
async def select_model(req: SelectModelRequest) -> ModelInfo:
    state.logs.append(level="INFO", event="model.select", msg=f"Selecting model: {req.modelId}")
    try:
        model_id = _validated_model_id(req.modelId)
    except ValueError as e:
        state.logs.append(level="ERROR", event="model.invalid", msg=str(e), fields={"modelId": req.modelId})
        raise HTTPException(status_code=400, detail=str(e)) from e

    device = _device_str(state.engine.device)
    try:
        loaded = await asyncio.to_thread(runtime.load, model_id, device)
    except Exception as e:
        state.logs.append(level="ERROR", event="model.load_failed", msg=str(e), fields={"modelId": model_id})
        raise HTTPException(status_code=400, detail=str(e)) from e

    with state.lock:
        state.model_id = loaded.id
    state.logs.append(level="INFO", event="model.loaded", msg=f"Loaded model: {loaded.id}", fields={"task": loaded.task_type})
    return ModelInfo(id=loaded.id, filename=loaded.filename, taskType=loaded.task_type, names=loaded.names)


@app.post("/api/engine/select")
async def select_engine(req: SelectEngineRequest) -> dict[str, Any]:
    target = _device_str(req.device)
    try:
        ensure_device_available(target)
    except RuntimeError as e:
        state.logs.append(level="ERROR", event="engine.unavailable", msg=str(e), fields={"device": req.device})
        raise HTTPException(status_code=400, detail=str(e)) from e

    with state.lock:
        same = state.engine.device == req.device
        state.engine.warming = not same
    state.logs.append(level="INFO", event="engine.select", msg=f"Switching engine: {req.device}")

    if not same:
        try:
            cur = runtime.current()
            if cur is not None:
                # 仅 warmup（不重 load 权重）：用目标 device 跑一次空推理
                await asyncio.to_thread(runtime.warmup_current, target)
        except Exception as e:
            with state.lock:
                state.engine.warming = False
            state.logs.append(level="ERROR", event="engine.warmup_failed", msg=str(e), fields={"device": req.device})
            raise HTTPException(status_code=400, detail=f"Failed to switch to {req.device}: {e}") from e
        else:
            with state.lock:
                state.engine.device = req.device
                state.engine.warming = False

    return {"ok": True, "device": req.device, "warming": False}


@app.get("/api/params", response_model=Params)
def get_params() -> Params:
    return state.params


@app.post("/api/params", response_model=Params)
def update_params(req: UpdateParamsRequest) -> Params:
    with state.lock:
        state.params = Params(conf=req.conf, iou=req.iou, classFilter=req.classFilter, track=req.track)
        state.logs.append(level="INFO", event="params.update", msg="Updated params", fields=state.params.model_dump())
        return state.params


@app.get("/api/system/stats", response_model=SystemStats)
def api_system_stats() -> SystemStats:
    return system_stats()


# ---- Webhook 配置 + 派发 ----

@app.get("/api/webhook", response_model=WebhookConfig)
def api_webhook_get() -> WebhookConfig:
    return webhook_manager().get()


@app.post("/api/webhook", response_model=WebhookConfig)
def api_webhook_set(cfg: WebhookConfig) -> WebhookConfig:
    saved = webhook_manager().set(cfg)
    state.logs.append(level="INFO", event="webhook.config", msg="Webhook config updated", fields=saved.model_dump())
    return saved


@app.post("/api/webhook/test", response_model=NotifyResult)
async def api_webhook_test() -> NotifyResult:
    req = NotifyRequest(
        kind="test",
        level="INFO",
        title="Webhook 测试",
        msg="UltraConsole webhook 联通性测试。",
        fields={"source": "ultraconsole"},
    )
    res = await asyncio.to_thread(webhook_manager().dispatch, req)
    state.logs.append(
        level="INFO" if res.ok else "WARN",
        event="webhook.test",
        msg=f"test ok={res.ok}",
        fields=res.model_dump(),
    )
    return res


@app.post("/api/notify", response_model=NotifyResult)
async def api_notify(req: NotifyRequest) -> NotifyResult:
    res = await asyncio.to_thread(webhook_manager().dispatch, req)
    if res.ok:
        state.logs.append(
            level="INFO",
            event="notify.sent",
            msg=f"{req.kind} -> webhook",
            fields={"kind": req.kind, "level": req.level, "ref": req.ref, "httpStatus": res.httpStatus},
        )
    elif not res.skipped:
        state.logs.append(
            level="WARN",
            event="notify.failed",
            msg=res.reason or "unknown",
            fields={"kind": req.kind, "level": req.level, "ref": req.ref},
        )
    return res


def _decode_image(raw: bytes) -> Image.Image:
    if len(raw) > settings.max_upload_bytes:
        raise ValueError(f"Image exceeds {settings.max_upload_bytes} bytes")
    Image.MAX_IMAGE_PIXELS = settings.max_image_pixels
    img = Image.open(io.BytesIO(raw))
    if img.width <= 0 or img.height <= 0 or img.width * img.height > settings.max_image_pixels:
        raise ValueError(f"Image exceeds {settings.max_image_pixels} pixels")
    img.load()
    return img


def _run_infer(
    image: Image.Image,
    conf: float,
    iou: float,
    class_filter: list[str],
    track: bool = False,
    tracker_key: str | None = None,
) -> PredResponse:
    return runtime.infer_image(
        image=image,
        device=_device_str(state.engine.device),
        conf=conf,
        iou=iou,
        class_filter=class_filter,
        track=track,
        tracker_key=tracker_key,
    )


@app.get("/api/logs/export.csv")
def export_logs_csv() -> PlainTextResponse:
    csv_text = state.logs.export_csv()
    return PlainTextResponse(csv_text, media_type="text/csv")


@app.websocket("/ws/infer")
async def ws_infer(ws: WebSocket):
    authorized, subprotocol = await require_ws_auth(ws)
    if not authorized:
        return
    await ws.accept(subprotocol=subprotocol)
    state.logs.append(level="INFO", event="ws.connect", msg="Client connected")
    tracker_key = f"ws-infer:{uuid.uuid4().hex}"
    latest_frame: dict[str, Any] | None = None
    frame_ready = asyncio.Event()
    outbound: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=8)
    last_pred_perf: float | None = None
    no_model_logged = False

    def enqueue(payload: dict[str, Any]) -> None:
        if outbound.full():
            try:
                outbound.get_nowait()
            except asyncio.QueueEmpty:
                pass
        outbound.put_nowait(payload)

    def send_log(level: str, event: str, msg: str, fields: dict[str, Any] | None = None) -> None:
        entry = state.logs.append(level=level, event=event, msg=msg, fields=fields or {})
        enqueue({"type": "log", **entry.model_dump()})

    async def reader() -> None:
        nonlocal latest_frame
        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type")
            if mtype == "ping":
                enqueue({"type": "pong", "ts": time.time()})
                continue
            if mtype == "params":
                continue
            if mtype != "frame":
                continue
            b64 = msg.get("imageJpegBase64")
            if not isinstance(b64, str) or not b64:
                send_log("WARN", "infer.invalid_frame", "Missing JPEG payload")
                continue
            if len(b64) > settings.max_ws_base64_chars:
                send_log("WARN", "infer.frame_too_large", "WebSocket frame rejected")
                continue
            # Queue size 1：新帧直接覆盖尚未处理的旧帧。
            latest_frame = msg
            frame_ready.set()

    async def infer_worker() -> None:
        nonlocal latest_frame, last_pred_perf, no_model_logged
        while True:
            await frame_ready.wait()
            while latest_frame is not None:
                frame = latest_frame
                latest_frame = None
                frame_ready.clear()

                cur = runtime.current()
                if cur is None:
                    if not no_model_logged:
                        send_log("ERROR", "infer.no_model", "No model loaded")
                        no_model_logged = True
                    continue
                no_model_logged = False

                frame_id = str(frame.get("frameId", ""))
                ts = float(frame.get("ts", time.time()))
                b64 = str(frame["imageJpegBase64"])

                try:
                    raw = base64.b64decode(b64, validate=True)
                    img = await asyncio.to_thread(_decode_image, raw)
                    p = state.params
                    frame_params = Params(
                        conf=frame.get("conf", p.conf),
                        iou=frame.get("iou", p.iou),
                        classFilter=frame.get("classFilter", p.classFilter),
                        track=frame.get("track", p.track),
                    )
                    pred = await asyncio.to_thread(
                        _run_infer,
                        img,
                        frame_params.conf,
                        frame_params.iou,
                        list(frame_params.classFilter),
                        frame_params.track,
                        tracker_key,
                    )
                    now_perf = time.perf_counter()
                    if last_pred_perf is not None:
                        dt = max(1e-6, now_perf - last_pred_perf)
                        pred.telemetry.fps = 1.0 / dt
                    last_pred_perf = now_perf
                    pred.frameId = frame_id
                    pred.ts = ts
                    enqueue({"type": "pred", **pred.model_dump()})
                except Exception as e:
                    send_log("ERROR", "infer.ws_failed", str(e), {"frameId": frame_id})

    async def sender() -> None:
        while True:
            await ws.send_json(await outbound.get())

    tasks = [asyncio.create_task(reader()), asyncio.create_task(infer_worker()), asyncio.create_task(sender())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                raise exc
    except WebSocketDisconnect:
        state.logs.append(level="INFO", event="ws.disconnect", msg="Client disconnected")
    except Exception as e:
        state.logs.append(level="ERROR", event="ws.crash", msg=str(e))
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        runtime.release_tracker(tracker_key)
