from __future__ import annotations

import threading
from dataclasses import dataclass

from .config import settings
from .logging_store import LogStore
from .schemas import DeviceType, Params


@dataclass
class EngineState:
    device: DeviceType = "cpu"
    warming: bool = False


class AppState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.params = Params()
        self.engine = EngineState(device=settings.device if settings.device in ("cpu", "cuda") else "cpu")
        self.model_id: str | None = None
        self.logs = LogStore(maxlen=10_000)

