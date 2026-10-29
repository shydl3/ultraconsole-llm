from types import SimpleNamespace

import pytest
from app import devices


def test_cpu_is_always_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(devices.torch.cuda, "is_available", lambda: False)
    devices.ensure_device_available("cpu")


def test_unavailable_cuda_has_actionable_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(devices.torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(devices.torch.cuda, "device_count", lambda: 1)
    monkeypatch.setattr(devices.torch, "version", SimpleNamespace(cuda="12.8"))

    with pytest.raises(RuntimeError) as exc_info:
        devices.ensure_device_available("cuda")

    message = str(exc_info.value)
    assert "torch.cuda.device_count()=1" in message
    assert "torch.cuda.is_available() returns True" in message


def test_available_cuda_is_initialized(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(devices.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(devices.torch.cuda, "device_count", lambda: 1)
    monkeypatch.setattr(devices.torch.cuda, "init", lambda: calls.append("init"))
    monkeypatch.setattr(devices.torch.cuda, "set_device", lambda index: calls.append(index))

    devices.ensure_device_available("cuda")

    assert calls == ["init", 0]
