#!/usr/bin/env bash
set -Eeuo pipefail

SETUP=/opt/ultra/bin/setup.sh

case "${1:-serve}" in
  setup)
    exec "$SETUP"
    ;;
  serve)
    if ! "$SETUP" --check; then
      cat >&2 <<'MSG'

[ultraconsole] Python 依赖尚未安装或与当前镜像版本不匹配。

Windows 用户：双击「启动.bat」，它会自动完成安装。
命令行用户：在交付包目录执行
  docker compose run --rm setup
然后再执行
  docker compose up -d

MSG
      exit 78
    fi
    export PYTHONPATH="/app/backend:/app/Rules/src${PYTHONPATH:+:$PYTHONPATH}"
    cd /app
    exec /opt/ultra/envs/backend/bin/python -m uvicorn server:app \
      --host 0.0.0.0 --port 8001 --log-level info
    ;;
  shell)
    exec bash
    ;;
  *)
    exec "$@"
    ;;
esac
