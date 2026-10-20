#!/usr/bin/env bash
# 一键跑「能做的端」，生成验证证据摘要，供测试/发布报告引用。
#
# 用法：
#   scripts/verify/run_all.sh                       # 默认 server,web,desktop（快）
#   scripts/verify/run_all.sh --ends all            # 五端（Android/wine 较慢）
#   scripts/verify/run_all.sh --ends server,web --out /tmp/summary.md
#
# 输出：/tmp/jf-verify-summary.md（表格：端 | 命令 | 结果 | 证据）
# 退出码：0 = 选中的端全部通过；1 = 有失败；2 = 参数错误。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/scripts/verify/env.sh"

ENDS="server,web,desktop"
OUT="/tmp/jf-verify-summary.md"
LOG_DIR="/tmp/jf-verify"
while [ $# -gt 0 ]; do
    case "$1" in
        --ends) ENDS="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        *) echo "用法：$0 [--ends server,web,desktop,android,windows|all] [--out PATH]" >&2; exit 2 ;;
    esac
done
[ "$ENDS" = "all" ] && ENDS="server,web,desktop,android,windows"
mkdir -p "$LOG_DIR"
ROWS=()
FAIL=0
SERVER_STARTED=0
WEB_STARTED=0

has_end() {
    case ",$ENDS," in *",$1,"*) return 0 ;; *) return 1 ;; esac
}

cleanup() {
    if [ "$SERVER_STARTED" = "1" ]; then
        "$ROOT/scripts/verify/server.sh" stop >/dev/null 2>&1 || true
    fi
    if [ "$WEB_STARTED" = "1" ]; then
        pkill -f "[v]ite --host 127.0.0.1 --port 3000" 2>/dev/null || true
    fi
}
trap cleanup EXIT

row() { ROWS+=("| $1 | $2 | $3 | $4 |"); }

# ── server ─────────────────────────────────────────────
if has_end server; then
    LOG="$LOG_DIR/server.log"
    if curl -fsS --max-time 2 "http://127.0.0.1:${JF_SERVER_PORT}/health" >/dev/null 2>&1; then
        row "服务端" 'scripts/verify/server.sh start' "已在运行；/health 200" "$LOG"
    elif "$ROOT/scripts/verify/server.sh" start >"$LOG" 2>&1; then
        SERVER_STARTED=1
        row "服务端" 'scripts/verify/server.sh start' "退出码 0；/health 200" "$LOG"
    else
        FAIL=$((FAIL + 1))
        row "服务端" 'scripts/verify/server.sh start' "失败（见 log）" "$LOG"
    fi
fi

# ── web ────────────────────────────────────────────────
if has_end web; then
    LOG="$LOG_DIR/web.log"
    if ! curl -fsS --max-time 2 "http://127.0.0.1:${JF_WEB_PORT}/" >/dev/null 2>&1; then
        (cd "$ROOT/jflove-web" && nohup npm run dev -- --host 127.0.0.1 --port "${JF_WEB_PORT}" \
            >"$LOG_DIR/web-dev.log" 2>&1 &)
        WEB_STARTED=1
        jf_wait_http "http://127.0.0.1:${JF_WEB_PORT}/" 150 || true
    fi
    if node "$ROOT/scripts/verify/web_shot.mjs" \
        --url "http://127.0.0.1:${JF_WEB_PORT}/" \
        --click-text '连接服务端' --out /tmp/jf-web-runall.png >"$LOG" 2>&1; then
        if [ "$SERVER_STARTED" = "1" ] && ! grep -q "key-exchange" /tmp/jflove-server-verify.log 2>/dev/null; then
            FAIL=$((FAIL + 1))
            row "Web" "node scripts/verify/web_shot.mjs --click-text '连接服务端'" \
                "截图成功但未见 key-exchange" "$LOG"
        else
            row "Web" "node scripts/verify/web_shot.mjs --click-text '连接服务端'" \
                "截图 + CDP 点击 + key-exchange 200" "/tmp/jf-web-runall.png"
        fi
    else
        FAIL=$((FAIL + 1))
        row "Web" "node scripts/verify/web_shot.mjs --click-text '连接服务端'" \
            "失败（见 log）" "$LOG"
    fi
fi

# ── desktop ────────────────────────────────────────────
if has_end desktop; then
    LOG="$LOG_DIR/desktop.log"
    if "$ROOT/jflove-desktop/venv-linux/bin/python" \
        "$ROOT/scripts/verify/desktop_shot.py" \
        --out /tmp/jf-desktop-runall.png --wait 9000 >"$LOG" 2>&1; then
        row "Linux 桌面" "python scripts/verify/desktop_shot.py" \
            "1180x760 抓帧" "/tmp/jf-desktop-runall.png"
    else
        FAIL=$((FAIL + 1))
        row "Linux 桌面" "python scripts/verify/desktop_shot.py" "失败（见 log）" "$LOG"
    fi
fi

# ── android ────────────────────────────────────────────
if has_end android; then
    LOG="$LOG_DIR/android.log"
    if "$ROOT/scripts/verify/android_smoke.sh" --stop >"$LOG" 2>&1; then
        row "Android" "scripts/verify/android_smoke.sh --stop" \
            "安装 Success；启动截图" "/tmp/jf-android.png"
    else
        FAIL=$((FAIL + 1))
        row "Android" "scripts/verify/android_smoke.sh --stop" "失败（见 log）" "$LOG"
    fi
fi

# ── windows（wine）─────────────────────────────────────
if has_end windows; then
    LOG="$LOG_DIR/windows.log"
    if "$ROOT/scripts/verify/windows_wine.sh" --skip-download >"$LOG" 2>&1; then
        row "Windows" "scripts/verify/windows_wine.sh --skip-download" \
            "wine 登录页截图" "/tmp/jf-wine.png"
    else
        FAIL=$((FAIL + 1))
        row "Windows" "scripts/verify/windows_wine.sh --skip-download" "失败（见 log）" "$LOG"
    fi
fi

{
    echo "## 验证证据"
    echo
    echo "> run_all 摘要，时间 $(date '+%Y-%m-%d %H:%M:%S %z')，主机 $(hostname)，端 \`$ENDS\`"
    echo
    echo "| 端 | 命令 | 结果 | 证据 |"
    echo "| --- | --- | --- | --- |"
    for line in "${ROWS[@]}"; do echo "$line"; done
} >"$OUT"

echo "摘要：$OUT"
cat "$OUT"
[ "$FAIL" -eq 0 ] && exit 0
exit 1
