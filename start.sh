#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
RUNTIME_DIR="$ROOT_DIR/tmp/ultraconsole"
BACKEND_PORT=8001
FRONTEND_PORT=5173

usage() {
  cat <<'EOF'
用法：
  ./start.sh [前端端口]
  ./start.sh --port <前端端口>

示例：
  ./start.sh          # 前端端口 5173
  ./start.sh 3000     # 前端端口 3000
  ./start.sh -p 8080  # 前端端口 8080
EOF
}

case "${1:-}" in
  -h|--help)
    usage
    exit 0
    ;;
  -p|--port)
    if [[ $# -ne 2 ]]; then
      usage >&2
      exit 2
    fi
    FRONTEND_PORT="$2"
    ;;
  "")
    ;;
  *)
    if [[ $# -ne 1 ]]; then
      usage >&2
      exit 2
    fi
    FRONTEND_PORT="$1"
    ;;
esac

if [[ ! "$FRONTEND_PORT" =~ ^[0-9]+$ ]] || (( FRONTEND_PORT < 1 || FRONTEND_PORT > 65535 )); then
  echo "错误：前端端口必须是 1-65535 之间的整数。" >&2
  exit 2
fi

mkdir -p "$RUNTIME_DIR"

pid_is_running() {
  local pid_file="$1"
  local expected_dir="$2"
  local pid cwd
  [[ -f "$pid_file" ]] || return 1
  pid=""
  read -r pid < "$pid_file" || true
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
  [[ "$cwd" == "$expected_dir" ]]
}

port_is_in_use() {
  local port="$1"
  "$PYTHON_BIN" - "$port" <<'PY'
import socket
import sys

try:
    with socket.socket() as sock:
        # A recently stopped server can leave connections in TIME_WAIT. Real
        # servers (including uvicorn) use SO_REUSEADDR and can bind safely, so
        # the readiness preflight must use the same socket behavior.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", int(sys.argv[1])))
except OSError:
    raise SystemExit(0)
raise SystemExit(1)
PY
}

if [[ -x "$BACKEND_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$BACKEND_DIR/.venv/bin/python"
elif [[ -x "$BACKEND_DIR/venv/bin/python" ]]; then
  PYTHON_BIN="$BACKEND_DIR/venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  echo "错误：未找到 Python 3。请先按 README 创建后端虚拟环境。" >&2
  exit 1
fi

if ! "$PYTHON_BIN" -c 'import uvicorn' >/dev/null 2>&1; then
  echo "错误：当前 Python 环境未安装后端依赖。" >&2
  echo "请执行：$PYTHON_BIN -m pip install -r backend/requirements.txt" >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "错误：未找到 npm。" >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "错误：未找到 curl（启动就绪检查需要它）。" >&2
  exit 1
fi

if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
  echo "错误：前端依赖尚未安装，请先执行：cd frontend && npm ci" >&2
  exit 1
fi

backend_started=0
frontend_started=0
cleanup_failed_start() {
  local status=$?
  (( status == 0 )) && return
  echo "启动失败，正在清理本次已启动的进程……" >&2
  (( frontend_started == 1 )) && "$ROOT_DIR/stop.sh" --frontend-only >/dev/null 2>&1 || true
  (( backend_started == 1 )) && "$ROOT_DIR/stop.sh" --backend-only >/dev/null 2>&1 || true
}
trap cleanup_failed_start EXIT

BACKEND_PID_FILE="$RUNTIME_DIR/backend.pid"
FRONTEND_PID_FILE="$RUNTIME_DIR/frontend.pid"

if pid_is_running "$BACKEND_PID_FILE" "$BACKEND_DIR"; then
  echo "后端已在运行（PID $(<"$BACKEND_PID_FILE")，端口 $BACKEND_PORT）。"
else
  rm -f "$BACKEND_PID_FILE"
  if port_is_in_use "$BACKEND_PORT"; then
    echo "错误：后端端口 $BACKEND_PORT 已被其他进程占用。" >&2
    exit 1
  fi

  backend_args=(--host 127.0.0.1 --port "$BACKEND_PORT")
  if [[ -f "$BACKEND_DIR/.env" ]]; then
    backend_args+=(--env-file .env)
  fi
  : > "$RUNTIME_DIR/backend.log"
  (
    cd "$BACKEND_DIR"
    exec setsid "$PYTHON_BIN" -m uvicorn app.main:app "${backend_args[@]}" \
      >>"$RUNTIME_DIR/backend.log" 2>&1
  ) &
  backend_pid=$!
  echo "$backend_pid" > "$BACKEND_PID_FILE"
  backend_started=1

  backend_ready=0
  for _ in {1..60}; do
    if ! kill -0 "$backend_pid" 2>/dev/null; then
      echo "错误：后端进程提前退出，请查看 $RUNTIME_DIR/backend.log" >&2
      exit 1
    fi
    if curl --silent --fail --max-time 1 "http://127.0.0.1:$BACKEND_PORT/api/health" >/dev/null 2>&1; then
      backend_ready=1
      break
    fi
    sleep 1
  done
  if (( backend_ready == 0 )); then
    echo "错误：等待后端就绪超时，请查看 $RUNTIME_DIR/backend.log" >&2
    exit 1
  fi
  echo "后端已启动（PID $backend_pid，端口 $BACKEND_PORT）。"
fi

if pid_is_running "$FRONTEND_PID_FILE" "$FRONTEND_DIR"; then
  if [[ -f "$RUNTIME_DIR/frontend.port" ]]; then
    read -r running_port < "$RUNTIME_DIR/frontend.port"
  else
    running_port="未知"
  fi
  echo "前端已在运行（PID $(<"$FRONTEND_PID_FILE")，端口 $running_port）。"
  if [[ "$running_port" != "$FRONTEND_PORT" ]]; then
    echo "如需改为端口 $FRONTEND_PORT，请先执行 ./stop.sh。" >&2
    exit 1
  fi
else
  rm -f "$FRONTEND_PID_FILE" "$RUNTIME_DIR/frontend.port"
  if port_is_in_use "$FRONTEND_PORT"; then
    echo "错误：前端端口 $FRONTEND_PORT 已被其他进程占用。" >&2
    exit 1
  fi

  : > "$RUNTIME_DIR/frontend.log"
  (
    cd "$FRONTEND_DIR"
    exec setsid npm run dev -- --host 0.0.0.0 --port "$FRONTEND_PORT" --strictPort \
      >>"$RUNTIME_DIR/frontend.log" 2>&1
  ) &
  frontend_pid=$!
  echo "$frontend_pid" > "$FRONTEND_PID_FILE"
  echo "$FRONTEND_PORT" > "$RUNTIME_DIR/frontend.port"
  frontend_started=1

  frontend_ready=0
  for _ in {1..30}; do
    if ! kill -0 "$frontend_pid" 2>/dev/null; then
      echo "错误：前端进程提前退出，请查看 $RUNTIME_DIR/frontend.log" >&2
      exit 1
    fi
    if curl --silent --fail --max-time 1 "http://127.0.0.1:$FRONTEND_PORT/" >/dev/null 2>&1; then
      frontend_ready=1
      break
    fi
    sleep 1
  done
  if (( frontend_ready == 0 )); then
    echo "错误：等待前端就绪超时，请查看 $RUNTIME_DIR/frontend.log" >&2
    exit 1
  fi
  echo "前端已启动（PID $frontend_pid，端口 $FRONTEND_PORT）。"
fi

trap - EXIT
echo
echo "访问地址：http://localhost:$FRONTEND_PORT"
echo "日志目录：$RUNTIME_DIR"
echo "停止服务：./stop.sh"
