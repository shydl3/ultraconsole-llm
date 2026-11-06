#!/usr/bin/env bash
#
# 在 WSL / Linux 构建 UltraConsole 镜像并生成 Windows 交付包。
#
#   ./deliverable/build.sh                 # 构建 + 导出 tar.gz + 打包
#   ./deliverable/build.sh --no-save       # 只构建镜像
#   ./deliverable/build.sh --no-package    # 只构建/导出，不生成 Windows 包
#   TAG=1.0.1 ./deliverable/build.sh       # 指定版本
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/deliverable/VERSION")"
TAG="${TAG:-$VERSION}"
IMAGE="${IMAGE:-ultraconsole}"
SAVE=1
PACKAGE=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag) TAG="$2"; shift 2 ;;
    --no-save) SAVE=0; shift ;;
    --no-package) PACKAGE=0; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

command -v docker >/dev/null 2>&1 || { echo "错误：未找到 docker。" >&2; exit 1; }

echo "==> 构建镜像 ${IMAGE}:${TAG}"
docker build \
  -f "$ROOT/deliverable/Dockerfile" \
  --build-arg "ULTRA_VERSION=${TAG}" \
  --build-arg "APT_MIRROR=${APT_MIRROR:-https://mirrors.aliyun.com}" \
  --build-arg "NPM_REGISTRY=${NPM_REGISTRY:-https://registry.npmmirror.com}" \
  -t "${IMAGE}:${TAG}" \
  -t "${IMAGE}:latest" \
  "$ROOT"

if [[ "$SAVE" == "1" ]]; then
  mkdir -p "$ROOT/deliverable/dist/images"
  echo "==> 导出镜像（gzip，压缩级别 1 以缩短等待）"
  docker save "${IMAGE}:${TAG}" | gzip -1 > "$ROOT/deliverable/dist/images/${IMAGE}-${TAG}.tar.gz"
  ls -lh "$ROOT/deliverable/dist/images/${IMAGE}-${TAG}.tar.gz"
fi

if [[ "$PACKAGE" == "1" ]]; then
  TAG="$TAG" IMAGE="$IMAGE" "$ROOT/deliverable/make-package.sh"
fi

echo "==> 完成"
