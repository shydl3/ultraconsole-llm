#!/usr/bin/env bash
#
# UltraConsole 一键部署脚本（面向 Linux / WSL2 新手）
#
# 用法（在项目根目录执行）：
#   bash deploy.sh               # 部署并启动（前端默认端口 5100）
#   bash deploy.sh --port 8080   # 部署并启动，前端改用 8080 端口
#   bash deploy.sh --no-start    # 只完成安装部署，不启动服务
#
# 脚本会自动完成以下步骤（已完成的步骤会自动跳过，可放心重复执行）：
#   1. 安装系统依赖（curl、git、图形库等，需要输入一次 sudo 密码）
#   2. 安装 Miniconda（如果电脑上还没有 conda）
#   3. 创建名为 ultraconsole 的 Python 3.10 环境并安装全部 Python 依赖
#   4. 安装 Node.js 22（前端运行所需，已有则跳过）
#   5. 安装前端依赖（npm ci）
#   6. 下载模型权重：YOLO11m-Pose（约 40 MB）+ Qwen3-VL-2B（约 4 GB）+ Qwen3-VL-4B（约 9 GB）
#   7. 写入 backend/.env 配置（让 VLM 使用同一个 Python 环境）
#   8. 校验安装结果（PyTorch / CUDA / 模型文件）
#   9. 执行 ./start.sh 启动服务
#
# 所有下载均使用中国大陆可直连的镜像源（清华 PyPI / npmmirror / ModelScope）。

set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# ------------------------------------------------------------ 可配置项
PORT="${PORT:-5100}"                # 前端端口（后端固定 8001）
DO_START=1                          # 部署完成后是否自动启动
SKIP_APT=0                          # 是否跳过系统包安装
YOLO_SRC=""                         # 本地 yolo11m-pose.pt 路径（指定后不联网下载）
CONDA_DIR="${CONDA_DIR:-$HOME/miniconda3}"
ENV_NAME="${ENV_NAME:-ultraconsole}"
PY_VER="${PY_VER:-3.10}"
NODE_VERSION="${NODE_VERSION:-22.14.0}"
PIP_INDEX="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmmirror.com}"
CONDA_MIRROR="${CONDA_MIRROR:-https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main}"
MINICONDA_URL="${MINICONDA_URL:-https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-latest-Linux-x86_64.sh}"
QWEN_DIR="$ROOT/models/Qwen3-VL-2B-Instruct"
QWEN_4B_DIR="$ROOT/models/Qwen3-VL-4B-Instruct"
YOLO_DEST="$ROOT/backend/models/yolo11m-pose.pt"

CURRENT_STEP="初始化"
info() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[!] %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31m[错误] %s\033[0m\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
# 任何一步命令返回非 0 都会触发 ERR trap，打印“卡在哪一步 + 出错命令 + 排查建议”
on_error() {
  local code=$?
  fail "在【${CURRENT_STEP}】这一步中断了（脚本第 ${BASH_LINENO[0]} 行，退出码 ${code}）。
出错的命令：${BASH_COMMAND}
处理办法：先看上面的红色报错定位原因（常见：网络超时 / 磁盘空间不足 / 缺少 sudo 权限）；修复后重新执行 bash deploy.sh 即可，已完成的步骤会自动跳过。"
}
trap on_error ERR

usage() {
  cat <<'EOF'
用法：bash deploy.sh [选项]

  --no-start            只部署安装，不启动服务
  --port <端口>         前端端口（默认 5100，后端固定 8001）
  --skip-apt            跳过系统包安装（无 sudo 权限或已装好时使用）
  --yolo <文件>         使用本地 yolo11m-pose.pt 文件，不再联网下载
  --conda-dir <目录>    Miniconda 安装目录（默认 ~/miniconda3）
  --env-name <名称>     conda 环境名（默认 ultraconsole）
  -h, --help            显示帮助
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-start) DO_START=0; shift ;;
    --port) PORT="${2:?--port 需要端口号}"; shift 2 ;;
    --skip-apt) SKIP_APT=1; shift ;;
    --yolo) YOLO_SRC="${2:?--yolo 需要文件路径}"; shift 2 ;;
    --conda-dir) CONDA_DIR="${2:?--conda-dir 需要目录}"; shift 2 ;;
    --env-name) ENV_NAME="${2:?--env-name 需要名称}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "未知参数：$1（用 --help 查看用法）" ;;
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

info "开始部署 UltraConsole，项目目录：$ROOT"

# ------------------------------------------------------------ 1. 系统依赖
# libgl1 / libglib2.0：OpenCV（图像处理库）运行必需的系统图形库
CURRENT_STEP="[1/9] 安装系统依赖"
if [[ "$SKIP_APT" == "1" ]]; then
  warn "已按参数跳过系统包安装"
else
  if have sudo; then
    info "[1/9] 安装系统依赖（会提示输入一次 sudo 密码，输入时屏幕不显示是正常的）"
    if sudo -v && sudo apt-get update; then
      sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl ca-certificates xz-utils git libgl1 libgomp1 \
        || warn "部分系统包安装失败，继续"
      sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        libglib2.0-0t64 2>/dev/null \
        || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libglib2.0-0 \
        || warn "libglib2.0 安装失败，若后续 import cv2 报错请手动安装"
    else
      warn "sudo 验证或 apt update 失败，跳过系统包；若 import cv2 失败请手动安装 libgl1 libglib2.0-0"
    fi
  else
    warn "未找到 sudo，跳过系统包安装；若 import cv2 失败，请手动安装 libgl1 libglib2.0-0"
  fi
fi
have curl || fail "缺少 curl，请先安装：sudo apt-get install -y curl ca-certificates"

# ------------------------------------------------------------ 2. Miniconda
# conda 是 Python 环境管理器，用于创建与系统隔离的 Python 环境
setup_conda() {
  CURRENT_STEP="[2/9] 准备 conda（Python 环境管理器）"
  if [[ -x "$CONDA_DIR/bin/conda" ]]; then
    CONDA="$CONDA_DIR/bin/conda"
  elif have conda; then
    CONDA="$(command -v conda)"
    CONDA_DIR="$(dirname "$(dirname "$CONDA")")"
  else
    info "[2/9] 安装 Miniconda 到 $CONDA_DIR（约 140 MB，来自清华镜像）"
    local installer="/tmp/miniconda-$$.sh"
    curl -fL --retry 3 --connect-timeout 20 -o "$installer" "$MINICONDA_URL"
    bash "$installer" -b -p "$CONDA_DIR"
    rm -f "$installer"
    CONDA="$CONDA_DIR/bin/conda"
  fi
  [[ -x "$CONDA" ]] || fail "conda 不可用"
  CONDA_BASE="$("$CONDA" info --base)"
  # 写入 ~/.bashrc，让以后每次打开终端都能直接使用 conda 命令
  if ! grep -q 'conda.sh' "$HOME/.bashrc" 2>/dev/null; then
    printf '\n# ultraconsole conda\nsource "%s/etc/profile.d/conda.sh"\n' "$CONDA_BASE" >> "$HOME/.bashrc"
  fi
  info "conda 就绪：$("$CONDA" --version)"
}

env_prefix() {
  "$CONDA" env list | awk -v name="$ENV_NAME" '$1 == name {print $NF; exit}'
}

# ------------------------------------------------------------ 3. Python 环境与依赖
setup_env() {
  CURRENT_STEP="[3/9] 创建 Python 环境并安装依赖"
  local prefix
  prefix="$(env_prefix || true)"
  if [[ -n "$prefix" && -x "$prefix/bin/python" ]]; then
    info "[3/9] conda 环境已存在，跳过创建：$ENV_NAME"
  else
    info "[3/9] 创建 conda 环境 $ENV_NAME（Python $PY_VER，清华镜像）"
    "$CONDA" create -y -n "$ENV_NAME" --override-channels -c "$CONDA_MIRROR" "python=$PY_VER" \
      || "$CONDA" create -y -n "$ENV_NAME" "python=$PY_VER"
    prefix="$(env_prefix || true)"
  fi
  [[ -n "$prefix" && -x "$prefix/bin/python" ]] || fail "conda 环境创建失败：$ENV_NAME"
  ENV_PREFIX="$prefix"
  ENV_PY="$prefix/bin/python"
  ENV_BIN="$prefix/bin"
  info "Python：$("$ENV_PY" -V 2>&1)"

  info "安装 Python 依赖（PyTorch、后端、Qwen 推理等，首次约 6-8 GB，耗时较长请耐心等待）"
  if ! "$ENV_PY" -m pip --version >/dev/null 2>&1; then
    "$CONDA" install -y -n "$ENV_NAME" pip || "$ENV_PY" -m ensurepip || fail "无法安装 pip"
  fi
  "$ENV_PY" -m pip install --upgrade pip -i "$PIP_INDEX" --no-input
  "$ENV_PY" -m pip install -r "$ROOT/backend/requirements-wsl.txt" -i "$PIP_INDEX" --no-input
  "$ENV_PY" -m pip check || warn "pip check 报告了依赖冲突，请查看上方输出"
}

# ------------------------------------------------------------ 4. Node.js
node_major() {
  node -p 'Number(process.versions.node.split(".")[0])' 2>/dev/null || echo 0
}

setup_node() {
  CURRENT_STEP="[4/9] 准备 Node.js（前端运行环境）"
  if have node && [[ "$(node_major)" -ge 20 ]]; then
    info "[4/9] 使用系统已有 Node $(node -v)"
    return
  fi
  info "[4/9] 安装 Node.js $NODE_VERSION 到 $HOME/.local/node（npmmirror 镜像）"
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

# ------------------------------------------------------------ 5. 前端依赖
setup_frontend() {
  CURRENT_STEP="[5/9] 安装前端依赖"
  info "[5/9] 安装前端依赖（npm ci，npmmirror 镜像）"
  ( cd "$ROOT/frontend" && npm ci --registry="$NPM_REGISTRY" --no-audit --no-fund )
}

# ------------------------------------------------------------ 6. 模型权重
download_yolo() {
  CURRENT_STEP="[6/9] 下载 YOLO11 姿态模型权重"
  if [[ -n "$YOLO_SRC" ]]; then
    [[ -f "$YOLO_SRC" ]] || fail "指定的 YOLO 文件不存在：$YOLO_SRC"
    mkdir -p "$(dirname "$YOLO_DEST")"
    cp -f "$YOLO_SRC" "$YOLO_DEST"
    info "已复制本地 YOLO11 权重：$YOLO_DEST"
    return
  fi
  if [[ -s "$YOLO_DEST" ]] && (( $(stat -c%s "$YOLO_DEST") > 1000000 )); then
    info "YOLO11 权重已存在，跳过下载：$YOLO_DEST"
    return
  fi
  mkdir -p "$(dirname "$YOLO_DEST")"
  local url part="$YOLO_DEST.part"
  for url in \
    "https://modelscope.cn/models/Ultralytics/YOLO11/resolve/master/yolo11m-pose.pt" \
    "https://hf-mirror.com/Ultralytics/YOLO11/resolve/main/yolo11m-pose.pt" \
    "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11m-pose.pt"; do
    info "下载 YOLO11 权重（约 40 MB）：$url"
    if curl -fL --retry 3 --connect-timeout 20 --progress-bar -o "$part" "$url" \
        && [[ -s "$part" ]] && (( $(stat -c%s "$part") > 1000000 )); then
      mv "$part" "$YOLO_DEST"
      info "YOLO11 就绪：$YOLO_DEST"
      return
    fi
    rm -f "$part"
    warn "该下载源失败，尝试下一个"
  done
  fail "YOLO11 下载失败；请用 --yolo /path/to/yolo11m-pose.pt 指定本地文件"
}

# 下载指定 Qwen3-VL 权重（ModelScope 主源 / hf-mirror 备源）；已完整则自动跳过、支持断点续传
fetch_qwen() {
  local repo="$1" target="$2"
  MODELS_DIR="$ROOT/models" \
  VLM_MODEL_REPO="$repo" \
  VLM_MODEL_PATH="$target" \
  YOLO_PATH="$YOLO_DEST" \
  VLM_MODEL_SOURCE=modelscope \
  HF_ENDPOINT=https://hf-mirror.com \
  PYTHONPATH="$ROOT/Rules/src" \
    "$ENV_PY" "$ROOT/scripts/download_models.py"
}

download_qwen() {
  CURRENT_STEP="[6/9] 下载 Qwen3-VL 视觉大模型（2B + 4B，共约 13 GB）"
  info "[6/9] 检查/下载 Qwen3-VL-2B-Instruct（ModelScope，约 4 GB，耗时取决于网速）"
  fetch_qwen "Qwen/Qwen3-VL-2B-Instruct" "$QWEN_DIR"
  info "[6/9] 检查/下载 Qwen3-VL-4B-Instruct（ModelScope，约 9 GB，耗时取决于网速）"
  # 4B 体积大且非运行必需（默认用 2B）：下载失败不阻断部署，重跑 deploy.sh 会自动续传
  if ! fetch_qwen "Qwen/Qwen3-VL-4B-Instruct" "$QWEN_4B_DIR"; then
    warn "Qwen3-VL-4B 未下载完成；服务仍可用 2B 正常启动。稍后重新执行 bash deploy.sh 会自动续传 4B。"
  fi
}

# ------------------------------------------------------------ 7. 写入配置
write_env() {
  CURRENT_STEP="[7/9] 写入后端配置 backend/.env"
  local file="$ROOT/backend/.env"
  touch "$file"
  # 单环境模式：VLM 推理与后端使用同一个 conda 环境的 Python
  if grep -q '^VLM_PYTHON=' "$file"; then
    sed -i "s|^VLM_PYTHON=.*|VLM_PYTHON=$ENV_PY|" "$file"
  else
    printf 'VLM_PYTHON=%s\n' "$ENV_PY" >> "$file"
  fi
  info "[7/9] 已写入 backend/.env：VLM_PYTHON=$ENV_PY"
}

# ------------------------------------------------------------ 8. 校验
validate() {
  CURRENT_STEP="[8/9] 校验依赖与模型"
  info "[8/9] 校验依赖与模型是否安装成功"
  QWEN_DIR="$QWEN_DIR" QWEN_4B_DIR="$QWEN_4B_DIR" PYTHONPATH="$ROOT/backend:$ROOT/Rules/src" "$ENV_PY" - <<'PY'
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
print("model 2B    :", validate_model_path(os.environ["QWEN_DIR"]))
try:
    print("model 4B    :", validate_model_path(os.environ["QWEN_4B_DIR"]))
except ValueError as exc:
    print("model 4B    : 未就绪（可稍后重跑 deploy.sh 续传）->", exc)
PY
  [[ -s "$YOLO_DEST" ]] || fail "YOLO11 缺失：$YOLO_DEST"
  info "YOLO11：$YOLO_DEST"
}

# ------------------------------------------------------------ 9. 启动
setup_conda
setup_env
setup_node
setup_frontend
download_yolo
download_qwen
write_env
validate

if ! have nvidia-smi; then
  warn "未检测到 nvidia-smi：请安装 NVIDIA 驱动（WSL2 用户请安装 Windows 侧的 NVIDIA 驱动）；Qwen 推理需要 CUDA。"
elif ! "$ENV_PY" -c 'import sys, torch; sys.exit(0 if torch.cuda.is_available() else 1)' 2>/dev/null; then
  warn "CUDA 当前不可用：若刚更新驱动，WSL2 用户请在 Windows PowerShell 执行 wsl --shutdown 后重新打开终端再试。"
fi

if [[ "$DO_START" == "1" ]]; then
  CURRENT_STEP="[9/9] 启动服务"
  info "[9/9] 启动 UltraConsole（前端端口 $PORT）"
  export PATH="$ENV_BIN:$PATH"
  export ULTRA_CONDA_ENV="$ENV_NAME"   # 让 start.sh 也能定位自定义环境名
  ./start.sh "$PORT"
  URL="http://localhost:$PORT"
  printf '\n\033[1;32m部署完成，服务已启动：%s\033[0m\n' "$URL"
  if have wslview; then
    wslview "$URL" >/dev/null 2>&1 || true
  elif have cmd.exe; then
    cmd.exe /c start "" "$URL" >/dev/null 2>&1 || true
  fi
else
  printf '\n\033[1;32m部署完成（未启动）。\033[0m\n启动命令：\n  conda activate %s\n  ./start.sh %s\n' "$ENV_NAME" "$PORT"
fi
