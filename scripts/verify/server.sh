#!/usr/bin/env bash
# 服务端「启动即验」入口：临时库 + run_server.py + /health
#
# 用法：
#   scripts/verify/server.sh start     # 启动（默认），退出前保留进程
#   scripts/verify/server.sh stop
#   scripts/verify/server.sh status
#   scripts/verify/server.sh smoke     # 启动 → health → 停止（CI 友好）
#
# 判据：start 成功时打印 {"status":"ok"}；smoke 退出码 0。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/scripts/verify/env.sh"

DB="${JFLOVE_DB_PATH:-/tmp/jflove-verify.db}"
PORT="${JFLOVE_PORT:-$JF_SERVER_PORT}"
PIDFILE="/tmp/jflove-server-verify.pid"
LOG="/tmp/jflove-server-verify.log"

health() { curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/health"; }

case "${1:-start}" in
    start)
        if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            jf_pass "已在运行（pid=$(cat "$PIDFILE")），直接探活"
            health && echo
            exit 0
        fi
        # 必须用 run_server.py：uvicorn 直启会绕过 JFLOVE_DB_PATH，碰真实 dev 库
        cp "$ROOT/jflove-db/jflove-dev.db" "$DB"
        (
            cd "$ROOT/jflove-server"
            JFLOVE_DB_PATH="$DB" JFLOVE_PORT="$PORT" \
                nohup venv-linux/bin/python run_server.py >"$LOG" 2>&1 &
            echo $! >"$PIDFILE"
        )
        if jf_wait_http "http://127.0.0.1:${PORT}/health" 100; then
            health && echo
            jf_pass "DB=$DB PORT=$PORT PID=$(cat "$PIDFILE")"
            exit 0
        fi
        echo "[verify] 服务端启动失败：" >&2
        tail -20 "$LOG" >&2
        exit 1
        ;;
    stop)
        if [ -f "$PIDFILE" ]; then
            kill "$(cat "$PIDFILE")" 2>/dev/null || true
            rm -f "$PIDFILE"
            jf_pass "已停止"
        else
            jf_pass "没有 pidfile，跳过"
        fi
        ;;
    status)
        if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            jf_pass "running pid=$(cat "$PIDFILE")"
            health && echo
        else
            jf_pass "stopped"
            exit 1
        fi
        ;;
    smoke)
        trap 'kill "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null || true; rm -f "$PIDFILE"' EXIT
        "$0" start
        jf_pass "smoke ok"
        ;;
    *)
        jf_die "用法：$0 [start|stop|status|smoke]"
        ;;
esac
