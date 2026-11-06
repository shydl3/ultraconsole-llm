#!/usr/bin/env bash
#
# 全新 WSL (Ubuntu) 一键初始化 UltraConsole 开发环境。
#
#   bash scripts/bootstrap-wsl.sh              # 安装 + 启动
#   bash scripts/bootstrap-wsl.sh --no-start   # 只安装，不启动
#   bash scripts/bootstrap-wsl.sh --yolo /path/to/yolo11m-pose.pt
#
# 默认全部走大陆可直连源：
#   Miniconda/conda 包 -> 清华镜像
#   pip / torch       -> 清华 PyPI
#   Node / npm        -> npmmirror
#   Qwen / YOLO11     -> ModelScope
#
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${PORT:-5100}"
DO_START=1
SKIP_APT=0
YOLO_SRC=""
CONDA_DIR="${CONDA_DIR:-$HOME/miniconda3}"
ENV_NAME="${ENV_NAME:-ultraconsole}"
PY_VER="${PY_VER:-3.10}"
NODE_VERSION="${NODE_VERSION:-22.14.0}"
PIP_INDEX="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmmirror.com}"
CONDA_MIRROR="${CONDA_MIRROR:-https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main}"
MINICONDA_URL="${MINICONDA_URL:-https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-latest-Linux-x86_64.sh}"
QWEN_DIR="$ROOT/models/Qwen3-VL-2B-Instruct"
YOLO_DEST="$ROOT/backend/models/yolo11m-pose.pt"

info() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[!] %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31m[错误] %s\033[0m\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
trap 'fail "第 $LINENO 行执行失败：$BASH_COMMAND"' ERR

usage() {
  cat <<'EOF'
用法：bash scripts/bootstrap-wsl.sh [选项]

  --no-start            只安装，不启动服务
  --port <端口>         启动端口（默认 5100）
  --skip-apt            跳过系统包安装（无 sudo 或已装好 libGL 时）
  --yolo <文件>         使用本地 yolo11m-pose.pt，不再联网下载
  --conda-dir <目录>    Miniconda 安装目录（默认 ~/miniconda3）
  --env-name <名称>     conda 环境名（默认 ultraconsole）
  -h, --help            显示帮助
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-start) DO_START=0; shift ;;
    --port) PORT="${2:?--port 需要端口}"; shift 2 ;;
    --skip-apt) SKIP_APT=1; shift ;;
    --yolo) YOLO_SRC="${2:?--yolo 需要文件路径}"; shift 2 ;;
    --conda-dir) CONDA_DIR="${2:?--conda-dir 需要目录}"; shift 2 ;;
    --env-name) ENV_NAME="${2:?--env-name 需要名称}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "未知参数：$1" ;;
  esac
done

[[ "$PORT" =~ ^[0-9]+$ ]] || fail "端口必须是数字：$PORT"
[[ -f /etc/os-release ]] || fail "未找到 /etc/os-release，当前不是标准 Linux 发行版"
# shellcheck disable=SC1091
. /etc/os-release
case "${ID:-}" in
  ubuntu|debian) ;;
  *) warn "当前系统 ${PRETTY_NAME:-$ID} 未在 Ubuntu/Debian 上验证，继续尝试" ;;
esac

info "项目目录：$ROOT"

# ---------------------------------------------------------------- 系统依赖
if [[ "$SKIP_APT" == "1" ]]; then
  warn "已跳过 apt 安装"
else
  if have sudo; then
    info "安装系统依赖（需要输入一次 sudo 密码）"
    if sudo -v && sudo apt-get update; then
      sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl ca-certificates xz-utils git libgl1 libgomp1 \
        || warn "部分系统包安装失败，继续"
      sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        libglib2.0-0t64 2>/dev/null \
        || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libglib2.0-0 \
        || warn "libglib2.0 安装失败，若 import cv2 报错请手动安装"
    else
      warn "sudo 验证或 apt update 失败，跳过系统包；若 import cv2 失败请手动安装 libgl1 libglib2.0-0"
    fi
  else
    warn "未找到 sudo，跳过 apt；若 import cv2 失败，请手动安装 libgl1 libglib2.0-0"
  fi
fi
have curl || fail "缺少 curl，请先安装：sudo apt-get install -y curl ca-certificates"

# ---------------------------------------------------------------- Miniconda
setup_conda() {
  if [[ -x "$CONDA_DIR/bin/conda" ]]; then
    CONDA="$CONDA_DIR/bin/conda"
  elif have conda; then
    CONDA="$(command -v conda)"
    CONDA_DIR="$(dirname "$(dirname "$CONDA")")"
  else
    info "安装 Miniconda 到 $CONDA_DIR"
    local installer="/tmp/miniconda-$$.sh"
    curl -fL --retry 3 --connect-timeout 20 -o "$installer" "$MINICONDA_URL"
    bash "$installer" -b -p "$CONDA_DIR"
    rm -f "$installer"
    CONDA="$CONDA_DIR/bin/conda"
  fi
  [[ -x "$CONDA" ]] || fail "conda 不可用"
  CONDA_BASE="$("$CONDA" info --base)"
  if ! grep -q 'conda.sh' "$HOME/.bashrc" 2>/dev/null; then
    printf '\n# ultraconsole conda\nsource "%s/etc/profile.d/conda.sh"\n' "$CONDA_BASE" >> "$HOME/.bashrc"
  fi
  info "conda：$("$CONDA" --version)"
}

env_prefix() {
  "$CONDA" env list | awk -v name="$ENV_NAME" '$1 == name {print $NF; exit}'
}

setup_env() {
  local prefix
  prefix="$(env_prefix || true)"
  if [[ -n "$prefix" && -x "$prefix/bin/python" ]]; then
    info "conda 环境已存在：$ENV_NAME"
  else
    info "创建 conda 环境 $ENV_NAME（python $PY_VER，清华镜像）"
    "$CONDA" create -y -n "$ENV_NAME" --override-channels -c "$CONDA_MIRROR" "python=$PY_VER" \
      || "$CONDA" create -y -n "$ENV_NAME" "python=$PY_VER"
    prefix="$(env_prefix || true)"
  fi
  [[ -n "$prefix" && -x "$prefix/bin/python" ]] || fail "conda 环境创建失败：$ENV_NAME"
  ENV_PREFIX="$prefix"
  ENV_PY="$prefix/bin/python"
  ENV_BIN="$prefix/bin"
  info "Python：$("$ENV_PY" -V 2>&1)"
}

setup_python_deps() {
  info "安装 Python 依赖（首次约 6-8 GB，使用清华 PyPI 镜像）"
  if ! "$ENV_PY" -m pip --version >/dev/null 2>&1; then
    "$CONDA" install -y -n "$ENV_NAME" pip || "$ENV_PY" -m ensurepip || fail "无法安装 pip"
  fi
  "$ENV_PY" -m pip install --upgrade pip -i "$PIP_INDEX" --no-input
  "$ENV_PY" -m pip install -r "$ROOT/backend/requirements-wsl.txt" -i "$PIP_INDEX" --no-input
  "$ENV_PY" -m pip check || warn "pip check 报告了依赖冲突，请查看上方输出"
}

# ---------------------------------------------------------------- Node / 前端
node_major() {
  node -p 'Number(process.versions.node.split(".")[0])' 2>/dev/null || echo 0
}

setup_node() {
  if have node && [[ "$(node_major)" -ge 20 ]]; then
    info "使用系统 Node $(node -v)"
    return
  fi
  info "安装 Node $NODE_VERSION 到 $HOME/.local/node（npmmirror）"
  local url="https://npmmirror.com/mirrors/node/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz"
  local tmp="/tmp/node-$$.tar.xz"
  curl -fL --retry 3 --connect-timeout 20 -o "$tmp" "$url"
  rm -rf "$HOME/.local/node"
  mkdir -p "$HOME/.local/node"
  tar -xJf "$tmp" -C "$HOME/.local/node" --strip-components=1
  rm -f "$tmp"
  export PATH="$HOME/.local/node/bin:$PATH"
  mkdir -p "$HOME/.local/bin"
  ln -sf "$HOME/.local/node/bin/node" "$HOME/.local/bin/node"
  ln -sf "$HOME/.local/node/bin/npm" "$HOME/.local/bin/npm"
  ln -sf "$HOME/.local/node/bin/npx" "$HOME/.local/bin/npx"
  if ! grep -q '# ultraconsole node' "$HOME/.bashrc" 2>/dev/null; then
    printf '\n# ultraconsole node\nexport PATH="$HOME/.local/node/bin:$PATH"\n' >> "$HOME/.bashrc"
  fi
  have node || fail "Node 安装失败"
  info "Node：$(node -v)，npm：$(npm -v)"
}

setup_frontend() {
  info "安装前端依赖（npm ci，npmmirror）"
  ( cd "$ROOT/frontend" && npm ci --registry="$NPM_REGISTRY" --no-audit --no-fund )
}

# ---------------------------------------------------------------- 模型权重
download_yolo() {
  if [[ -n "$YOLO_SRC" ]]; then
    [[ -f "$YOLO_SRC" ]] || fail "指定的 YOLO 文件不存在：$YOLO_SRC"
    mkdir -p "$(dirname "$YOLO_DEST")"
    cp -f "$YOLO_SRC" "$YOLO_DEST"
    info "已复制 YOLO11：$YOLO_DEST"
    return
  fi
  if [[ -s "$YOLO_DEST" ]] && (( $(stat -c%s "$YOLO_DEST") > 1000000 )); then
    info "YOLO11 已存在：$YOLO_DEST"
    return
  fi
  mkdir -p "$(dirname "$YOLO_DEST")"
  local url part="$YOLO_DEST.part"
  for url in \
    "https://modelscope.cn/models/Ultralytics/YOLO11/resolve/master/yolo11m-pose.pt" \
    "https://hf-mirror.com/Ultralytics/YOLO11/resolve/main/yolo11m-pose.pt" \
    "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11m-pose.pt"; do
    info "下载 YOLO11：$url"
    if curl -fL --retry 3 --connect-timeout 20 --progress-bar -o "$part" "$url" \
        && [[ -s "$part" ]] && (( $(stat -c%s "$part") > 1000000 )); then
      mv "$part" "$YOLO_DEST"
      info "YOLO11 就绪：$YOLO_DEST"
      return
    fi
    rm -f "$part"
    warn "该源失败，尝试下一个"
  done
  fail "YOLO11 下载失败；请用 --yolo /path/to/yolo11m-pose.pt 指定本地文件"
}

download_qwen() {
  info "检查/下载 Qwen3-VL（ModelScope，约 4 GB）"
  MODELS_DIR="$ROOT/models" \
  VLM_MODEL_PATH="$QWEN_DIR" \
  YOLO_PATH="$YOLO_DEST" \
  VLM_MODEL_SOURCE=modelscope \
  HF_ENDPOINT=https://hf-mirror.com \
  PYTHONPATH="$ROOT/Rules/src" \
    "$ENV_PY" "$ROOT/deliverable/docker/download_models.py"
}

write_env() {
  local file="$ROOT/backend/.env"
  touch "$file"
  if grep -q '^VLM_PYTHON=' "$file"; then
    sed -i "s|^VLM_PYTHON=.*|VLM_PYTHON=$ENV_PY|" "$file"
  else
    printf 'VLM_PYTHON=%s\n' "$ENV_PY" >> "$file"
  fi
  info "已写入 backend/.env：VLM_PYTHON=$ENV_PY"
}

validate() {
  info "校验依赖与模型"
  QWEN_DIR="$QWEN_DIR" PYTHONPATH="$ROOT/backend:$ROOT/Rules/src" "$ENV_PY" - <<'PY'
import os
import sys

import av
import cv2
import torch
import transformers
import ultralytics
from ultra_rules.vlm.models import validate_model_path

print("python      :", sys.version.split()[0])
print("torch       :", torch.__version__)
print("transformers:", transformers.__version__)
print("ultralytics :", ultralytics.__version__)
print("cuda        :", torch.cuda.is_available(), "| devices:", torch.cuda.device_count())
print("model       :", validate_model_path(os.environ["QWEN_DIR"]))
PY
  [[ -s "$YOLO_DEST" ]] || fail "YOLO11 缺失：$YOLO_DEST"
  info "YOLO11：$YOLO_DEST"
}

# ---------------------------------------------------------------- 主流程
setup_conda
setup_env
setup_python_deps
setup_node
setup_frontend
download_yolo
download_qwen
write_env
validate

if ! have nvidia-smi; then
  warn "未检测到 nvidia-smi：请安装 NVIDIA WSL 驱动；Qwen 需要 CUDA/BF16。"
elif ! "$ENV_PY" -c 'import sys, torch; sys.exit(0 if torch.cuda.is_available() else 1)' 2>/dev/null; then
  warn "CUDA 不可用：若刚更新驱动，请在 Windows 执行 wsl --shutdown 后重开 WSL。"
fi

if [[ "$DO_START" == "1" ]]; then
  info "启动 UltraConsole（端口 $PORT）"
  export PATH="$ENV_BIN:$PATH"
  ./start.sh "$PORT"
  URL="http://localhost:$PORT"
  printf '\n\033[1;32m已启动：%s\033[0m\n' "$URL"
  if have wslview; then
    wslview "$URL" >/dev/null 2>&1 || true
  elif have cmd.exe; then
    cmd.exe /c start "" "$URL" >/dev/null 2>&1 || true
  fi
else
  printf '\n安装完成。启动命令：\n  conda activate %s\n  ./start.sh %s\n' "$ENV_NAME" "$PORT"
fi
