#!/usr/bin/env bash
#
# 安装/校验运行依赖（PyTorch + 后端 + Qwen）并下载模型权重。
#
# 依赖安装在 /opt/ultra/envs 卷里，镜像本身保持轻量；requirements 或
# torch 版本变化时，本脚本会重建两个虚拟环境。
set -Eeuo pipefail

ENVS_DIR=/opt/ultra/envs
PIP_CACHE=/opt/ultra/pip-cache
REQ_BACKEND=/app/backend/requirements.txt
REQ_VLM=/opt/ultra/requirements/vlm.txt
STAMP="$ENVS_DIR/.stamp"
SETUP_SCHEMA=1

TORCH_INDEX_URL="${TORCH_INDEX_URL:-https://download.pytorch.org/whl/cu124}"
TORCH_SPEC="${TORCH_SPEC:-torch torchvision}"
TORCH_VERSION="${TORCH_VERSION:-}"

export PIP_CACHE_DIR="$PIP_CACHE"
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PYTHONUTF8=1

log() { printf '\n[ultraconsole] %s\n' "$*"; }
die() { printf '\n[ultraconsole] 错误：%s\n' "$*" >&2; exit 1; }

compute_hash() {
  {
    printf 'schema=%s\n' "$SETUP_SCHEMA"
    printf 'python='; python3 -V
    printf 'torch_index=%s\n' "$TORCH_INDEX_URL"
    printf 'torch_spec=%s\n' "$TORCH_SPEC"
    printf 'torch_version=%s\n' "$TORCH_VERSION"
    cat "$REQ_BACKEND" "$REQ_VLM"
  } | sha256sum | cut -d' ' -f1
}

if [[ "${1:-}" == "--check" ]]; then
  [[ -f "$STAMP" && -x "$ENVS_DIR/backend/bin/python" && -x "$ENVS_DIR/qwen/bin/python" ]] || exit 1
  [[ "$(cat "$STAMP")" == "$(compute_hash)" ]] || exit 1
  exit 0
fi

mkdir -p "$ENVS_DIR" "$PIP_CACHE" /models/detect \
  /data/vlm /data/video-previews /data/.hf /data/.ultralytics \
  /data/.cache /data/.torch /data/.matplotlib /data/home

# 防止双击两次导致两个 setup 并发写同一个卷。
if command -v flock >/dev/null 2>&1; then
  exec 9>"$ENVS_DIR/.setup.lock"
  flock 9
fi

WANTED_HASH="$(compute_hash)"
if [[ -f "$STAMP" && "$(cat "$STAMP")" == "$WANTED_HASH" \
      && -x "$ENVS_DIR/backend/bin/python" && -x "$ENVS_DIR/qwen/bin/python" ]]; then
  log "Python 依赖已是最新，跳过安装"
else
  PIP_ARGS=()
  if [[ "${PIP_NO_INDEX:-0}" != "1" && -n "${PIP_INDEX_URL:-}" ]]; then
    PIP_ARGS+=(--index-url "$PIP_INDEX_URL")
  fi
  [[ -d /opt/ultra/wheelhouse ]] && PIP_ARGS+=(--find-links /opt/ultra/wheelhouse)
  [[ "${PIP_NO_INDEX:-0}" == "1" ]] && PIP_ARGS+=(--no-index)

  create_env() {
    local name=$1
    log "创建虚拟环境：$name"
    python3 -m venv --clear "$ENVS_DIR/$name"
    if [[ "${PIP_NO_INDEX:-0}" != "1" ]]; then
      "$ENVS_DIR/$name/bin/python" -m pip install "${PIP_ARGS[@]}" --upgrade pip setuptools wheel
    fi
  }

  install_torch() {
    local python=$1
    local torch_args=("${PIP_ARGS[@]}")
    if [[ "${PIP_NO_INDEX:-0}" != "1" ]]; then
      torch_args+=(--index-url "$TORCH_INDEX_URL")
    fi
    log "安装 PyTorch（${TORCH_INDEX_URL}）"
    if [[ -n "$TORCH_VERSION" ]]; then
      "$python" -m pip install "${torch_args[@]}" "torch==${TORCH_VERSION}" torchvision
    else
      # shellcheck disable=SC2086  # TORCH_SPEC 允许包含多个包名
      "$python" -m pip install "${torch_args[@]}" $TORCH_SPEC
    fi
  }

  log "安装后端/姿态环境（首次约 3-4 GB）"
  create_env backend
  install_torch "$ENVS_DIR/backend/bin/python"
  "$ENVS_DIR/backend/bin/python" -m pip install "${PIP_ARGS[@]}" -r "$REQ_BACKEND"

  log "安装 Qwen 推理环境（复用 pip 缓存）"
  create_env qwen
  install_torch "$ENVS_DIR/qwen/bin/python"
  "$ENVS_DIR/qwen/bin/python" -m pip install "${PIP_ARGS[@]}" -r "$REQ_VLM"

  printf '%s\n' "$WANTED_HASH" > "$STAMP"
  log "Python 依赖安装完成"
fi

if [[ "${SKIP_MODEL_DOWNLOAD:-0}" == "1" ]]; then
  log "SKIP_MODEL_DOWNLOAD=1，跳过权重下载"
else
  log "检查模型权重（已存在则跳过）"
  MODELS_DIR=/models \
  VLM_MODEL_REPO="${VLM_MODEL_REPO:-Qwen/Qwen3-VL-2B-Instruct}" \
  VLM_MODEL_PATH="${VLM_MODEL_PATH:-/models/Qwen3-VL-2B-Instruct}" \
    "$ENVS_DIR/qwen/bin/python" /opt/ultra/bin/download_models.py
fi

MODEL_PATH="${VLM_MODEL_PATH:-/models/Qwen3-VL-2B-Instruct}"
log "校验模型：$MODEL_PATH"
PYTHONPATH=/app/Rules/src "$ENVS_DIR/qwen/bin/python" - "$MODEL_PATH" <<'PY'
import sys

from ultra_rules.vlm.models import validate_model_path

print("[ultraconsole] 模型可用：", validate_model_path(sys.argv[1]))
PY

log "准备完成。启动命令：docker compose up -d"
