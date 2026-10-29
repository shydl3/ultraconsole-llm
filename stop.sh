#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$ROOT_DIR/tmp/ultraconsole"
STOP_BACKEND=1
STOP_FRONTEND=1

case "${1:-}" in
  "") ;;
  --backend-only) STOP_FRONTEND=0 ;;
  --frontend-only) STOP_BACKEND=0 ;;
  -h|--help)
    echo "用法：./stop.sh"
    exit 0
    ;;
  *)
    echo "用法：./stop.sh" >&2
    exit 2
    ;;
esac

stop_service() {
  local name="$1"
  local pid_file="$2"
  local expected_dir="$3"
  local pid cwd

  if [[ ! -f "$pid_file" ]]; then
    echo "${name}未运行。"
    return
  fi

  pid=""
  read -r pid < "$pid_file" || true
  if [[ ! "$pid" =~ ^[0-9]+$ ]] || ! kill -0 "$pid" 2>/dev/null; then
    echo "${name}未运行（清理过期 PID 文件）。"
    rm -f "$pid_file"
    return
  fi

  # 防止 PID 被系统复用后误杀其他进程。
  cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
  if [[ "$cwd" != "$expected_dir" ]]; then
    echo "警告：$name 的 PID $pid 当前不属于本项目，已跳过停止。" >&2
    rm -f "$pid_file"
    return
  fi

  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  for _ in {1..10}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$pid_file"
      echo "${name}已停止。"
      return
    fi
    sleep 1
  done

  echo "${name}未能在 10 秒内退出，正在强制停止……" >&2
  kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
  rm -f "$pid_file"
  echo "${name}已停止。"
}

if (( STOP_FRONTEND == 1 )); then
  stop_service "前端" "$RUNTIME_DIR/frontend.pid" "$ROOT_DIR/frontend"
  rm -f "$RUNTIME_DIR/frontend.port"
fi

if (( STOP_BACKEND == 1 )); then
  stop_service "后端" "$RUNTIME_DIR/backend.pid" "$ROOT_DIR/backend"
fi
