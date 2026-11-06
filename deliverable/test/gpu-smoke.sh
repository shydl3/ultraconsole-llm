#!/usr/bin/env bash
#
# 在有 NVIDIA GPU 的机器上做端到端冒烟：健康检查、CUDA、NVENC、静态页面、Qwen 环境。
# 用法：PKG_DIR=/path/to/UltraConsole-1.0.0-Win64 ./deliverable/test/gpu-smoke.sh
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
PKG_DIR="${PKG_DIR:-$ROOT/deliverable/dist/UltraConsole-$(tr -d '[:space:]' < "$ROOT/deliverable/VERSION")-Win64}"
PORT="${ULTRACONSOLE_PORT:-8001}"

[[ -d "$PKG_DIR" ]] || { echo "找不到交付目录：$PKG_DIR（先运行 build.sh）" >&2; exit 1; }

cd "$PKG_DIR"

echo "==> 安装依赖（已安装则快速跳过）"
docker compose run -T --rm setup

echo "==> 启动服务"
docker compose up -d

echo "==> 等待健康检查"
ready=0
for _ in $(seq 1 120); do
  if curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 5
done
[[ "$ready" == "1" ]] || { echo "服务未就绪，输出日志：" >&2; docker compose logs --tail 200; exit 1; }

echo "==> 静态前端"
curl -fsS "http://127.0.0.1:${PORT}/" | grep -qi '<div id="root"\|<html' || {
  echo "首页未返回 HTML" >&2; exit 1;
}

echo "==> 后端环境 / CUDA / NVENC"
docker compose exec -T app /opt/ultra/envs/backend/bin/python - <<'PY'
import torch
import av

print("torch:", torch.__version__, "cuda build:", torch.version.cuda)
assert torch.cuda.is_available(), "容器内 CUDA 不可用"
print("gpu:", torch.cuda.get_device_name(0))
assert "h264_nvenc" in av.codecs_available, "PyAV 未包含 h264_nvenc，浏览器预览转码会失败"
assert "h264_cuvid" in av.codecs_available, "PyAV 未包含 h264_cuvid，浏览器预览转码会失败"
print("h264_nvenc: True")
print("h264_cuvid: True")
PY

echo "==> Qwen 环境"
docker compose exec -T app /opt/ultra/envs/qwen/bin/python - <<'PY'
import torch
import transformers

print("torch:", torch.__version__, "transformers:", transformers.__version__)
assert torch.cuda.is_available(), "Qwen 环境看不到 CUDA"
PY

echo "==> 当前引擎状态"
curl -fsS "http://127.0.0.1:${PORT}/api/state"
echo
echo "冒烟测试通过。"
