#!/usr/bin/env bash
# Linux 桌面端源码 + CDP 交互验证（不依赖 X；用 offscreen 渲染）。
#
# 判据：打开本机终端 → 注入 `echo JFLove_LINUX_VERIFY` → xterm 行内出现该字符串。
# 证据：/tmp/jf-linux-ui/shot.png + 本脚本 stdout JSON。
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
HOST="${JF_CDP_HOST:-127.0.0.1}"
PORT="${JF_CDP_PORT:-9222}"
OUT="${JF_LINUX_UI_OUT:-/tmp/jf-linux-ui}"
mkdir -p "$OUT"
TEXT="${JF_UI_TEXT:-echo JFLove_LINUX_VERIFY}"

if ! curl -fsS -m 2 "http://$HOST:$PORT/json/version" >/dev/null 2>&1; then
    (cd jflove-desktop && QTWEBENGINE_REMOTE_DEBUGGING="$PORT" QT_QPA_PLATFORM=offscreen \
        timeout 300 venv-linux/bin/python src/main.py >/tmp/jf-linux-ui.log 2>&1 &)
    for _ in $(seq 1 40); do
        curl -fsS -m 2 "http://$HOST:$PORT/json/version" >/dev/null 2>&1 && break
        sleep 1
    done
fi
curl -fsS -m 3 "http://$HOST:$PORT/json/version" >/dev/null || { echo '{"status":"fail","reason":"cdp not ready"}'; exit 1; }

UI() { node scripts/verify/windows_vm_ui.mjs --host "$HOST" --port "$PORT" "$@"; }
UI --click-text '远程连接' >/dev/null
UI --click-text '本地' >/dev/null
UI --eval "(()=>{const b=[...document.querySelectorAll('button')].find(x=>(x.title||'').includes('·')); if(!b) return 'NO_LEAF'; b.dispatchEvent(new MouseEvent('dblclick',{bubbles:true})); return 'DBLCLICK';})()" >/dev/null
sleep 2
RESULT="$(node scripts/verify/cdp_keys.mjs --host "$HOST" --port "$PORT" --text "$TEXT")"
UI --shot "$OUT/shot.png" >/dev/null
echo "$RESULT" | grep -q 'JFLove_LINUX_VERIFY' || { echo "{\"status\":\"fail\",\"reason\":\"terminal output missing\",\"result\":$RESULT}"; exit 1; }
python3 - "$OUT/shot.png" <<'PY'
import json, sys
print(json.dumps({"status": "ok", "term": "JFLove_LINUX_VERIFY", "shot": sys.argv[1]}, ensure_ascii=False))
PY
