from __future__ import annotations

import os

import torch


def ensure_device_available(device: str) -> None:
    """Fail early with useful diagnostics when an inference device is unusable."""
    if device != "cuda":
        return

    try:
        available = torch.cuda.is_available()
        count = torch.cuda.device_count()
    except Exception as exc:
        raise RuntimeError(f"CUDA detection failed: {exc}") from exc

    if not available or count < 1:
        torch_version = getattr(torch, "__version__", "unknown")
        built_cuda = getattr(getattr(torch, "version", None), "cuda", None) or "none"
        visible_devices = os.environ.get("CUDA_VISIBLE_DEVICES", "not set")
        raise RuntimeError(
            "CUDA is not available to PyTorch "
            f"(torch={torch_version}, torch CUDA build={built_cuda}, "
            f"torch.cuda.device_count()={count}, CUDA_VISIBLE_DEVICES={visible_devices!r}). "
            "Check the NVIDIA driver and GPU passthrough (Docker/WSL), and install a CUDA-enabled "
            "PyTorch build compatible with the driver. Use CPU until torch.cuda.is_available() returns True."
        )

    try:
        # Force CUDA context initialization now, rather than failing during the first inference.
        torch.cuda.init()
        torch.cuda.set_device(0)
    except Exception as exc:
        raise RuntimeError(f"CUDA device 0 could not be initialized: {exc}") from exc
