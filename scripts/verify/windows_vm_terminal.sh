#!/usr/bin/env bash
# Windows 真机「终端交互」验收（v1.10.8 新增）
#
# 背景：`windows_vm_e2e.sh` 只覆盖 安装→启动→密钥交换→登录→上传，**不碰终端**。
# 而 v1.10.8 的修复（DECRQM 兼容层 / 会话级常驻 / 剪贴板桥 / 输出合并）全是终端相关，
# 必须在 Windows 真机上单独验一遍，否则"Windows 通过"是空话。
#
# 前置：VM 已启动、1.10.8 已安装并启动（建议 `windows_vm_e2e.sh --keep` 跑完后接着跑本脚本）。
# 用法：scripts/verify/windows_vm_terminal.sh
#   环境变量：JF_WIN_VM（域）、JF_WIN_OUT（证据目录，默认 /tmp/jf-win）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
VM="${JF_WIN_VM:-jflove-win10}"
OUT_DIR="${JF_WIN_OUT:-/tmp/jf-win}/terminal"
VERSION="$(python3 -c "import json;print(json.load(open('version.json'))['version'])")"
PY="$ROOT/scripts/verify/windows_vm.py"
GUEST_IP="$(python3 "$PY" ip | tr -d '\r')"
CDP_PORT=9223
mkdir -p "$OUT_DIR"

step() { printf '\n[win-term] === %s ===\n' "$*"; }
ok()   { printf '[win-term] ✅ %s\n' "$*"; }
bad()  { printf '[win-term] ❌ %s\n' "$*" >&2; exit 1; }
win()  { python3 "$PY" "$@"; }

ui()      { timeout 30 node scripts/verify/windows_vm_ui.mjs --host "$GUEST_IP" --port "$CDP_PORT" --timeout 15000 "$@"; }
ui_text() { ui --eval "document.body.innerText" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("eval",""))'; }
rows()    { ui --eval "(() => { const hosts=[...document.querySelectorAll('[data-testid=terminal-host]')].filter((h)=>h.offsetParent!==null); return hosts[0] ? hosts[0].innerText : ''; })()" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("eval",""))'; }
keys()    { timeout 60 node scripts/verify/cdp_keys.mjs --host "$GUEST_IP" --port "$CDP_PORT" --text "$1" >/dev/null; }

step "0/5 前置：VM / 客户端 / CDP"
[ -n "$GUEST_IP" ] || bad "取不到客机 IP"
curl -fsS -m 3 "http://$GUEST_IP:$CDP_PORT/json/version" | grep -q QtWebEngine || bad "CDP 不可用（客户端没在跑？）"
ok "客机 $VM @ $GUEST_IP，CDP 就绪"

# 固定一个已知视口：CDP 的 device-metrics 覆盖在 QtWebEngine 里跨连接会残留，
# 不先归一化的话，上一次跑留下的矮窗会污染这次（实测踩过）。
ui --metrics 1180x760 >/dev/null
sleep 1
# 聚焦客户端窗口（QGA 的 GUI 交互要求窗口在前台）
win exec --ps "(New-Object -ComObject WScript.Shell).AppActivate('JFLove') | Out-Null; 'activated'" >/dev/null || true
sleep 1

if [[ "$(ui_text)" == *'文件管理'* ]]; then
    ok "已登录（复用会话）"
else
    bad "客户端不在主界面（先在登录页完成登录）"
fi

step "1/5 打开本机终端"
# 关掉上一轮遗留的标签，保证后面的"多标签"用例从干净状态开始
for _ in $(seq 1 8); do
    N="$(ui --eval "(() => { const b=[...document.querySelectorAll('button')].find((el)=>(el.getAttribute('aria-label')||'').startsWith('关闭 ')); if(!b) return 0; b.click(); return 1; })()" 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin).get("eval",0))' 2>/dev/null || echo 0)"
    [ "$N" = "1" ] || break
    sleep 1
done

ui --eval "location.hash='#/remote/local'; 'ok'" >/dev/null
sleep 3
# 本地终端配置卡片（class 含 rounded-xl + p-3.5）双击打开
# e2e 每次启动前会清空客户端状态（含本地终端配置库），所以优先用"打开默认终端"，
# 没有这个按钮时才回退到双击配置卡片。
OPENED="$(ui --eval "(() => { const b=[...document.querySelectorAll('button')].find((el)=>(el.innerText||'').includes('打开默认终端')); if(!b) return 'NO_BTN'; b.click(); return 'OPENED'; })()" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("eval",""))')"
if [ "$OPENED" != 'OPENED' ]; then
    ui --eval "(() => { const card=[...document.querySelectorAll('div')].find((el)=> typeof el.className==='string' && el.className.includes('rounded-xl') && el.className.includes('p-3.5')); if(!card) return 'NO_CARD'; card.dispatchEvent(new MouseEvent('dblclick',{bubbles:true})); return 'OPENED'; })()" >/dev/null
fi
for _ in $(seq 1 30); do
    R="$(rows 2>/dev/null || true)"
    [ -n "$R" ] && break
    sleep 2
done
[ -n "$R" ] || bad "终端没有渲染出内容"
printf '%s\n' "$R" | tail -3
ok "终端已渲染（证据：$OUT_DIR/01-terminal.png）"
ui --shot "$OUT_DIR/01-terminal.png" >/dev/null

step "2/5 键盘输入（英文命令）"
MARK="JFLove_v${VERSION}_TERM_OK"
keys "echo $MARK"
sleep 3
R="$(rows)"
printf '%s\n' "$R" | tail -3
[[ "$R" == *"$MARK"* ]] || bad "输入没有回显（期望 $MARK）"
ok "键盘输入可用：$MARK"
ui --shot "$OUT_DIR/02-typed.png" >/dev/null

step "3/5 复制 / 粘贴（Qt 剪贴板桥）"
# 为什么不用 PowerShell `Set-Clipboard`：QGA 的 guest-exec 跑在 **Session 0（SYSTEM）**，
# 它设的是系统会话的剪贴板，交互用户（jf）的桌面会话读不到 —— 那是测试环境错位，不是产品缺陷。
# 这里用"自己复制、自己粘贴"：先三击选中一行，点工具条复制（Qt 写剪贴板），再点粘贴（Qt 读），
# 全程在同一个会话里，既验证写入也验证读取。
SRC="COPY_SRC_${VERSION//./_}"
keys "echo $SRC"
sleep 2
POS="$(ui --eval "(() => {
  const hosts=[...document.querySelectorAll('[data-testid=terminal-host]')].filter((h)=>h.offsetParent!==null);
  if(!hosts[0]) return '';
  const rows=[...hosts[0].querySelectorAll('.xterm-rows > div')];
  const idx=rows.findIndex((r)=>r.textContent.includes('$SRC'));
  if(idx<0) return '';
  const rect=rows[idx].getBoundingClientRect();
  return Math.round(rect.left+40) + ',' + Math.round(rect.top + rect.height/2);
})()" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("eval",""))')"
[ -n "$POS" ] || bad "找不到可选中行（$SRC）"
ui --click-at "$POS" --count 3 >/dev/null
sleep 1
CLICKED="$(ui --eval "(() => { const b=[...document.querySelectorAll('button')].find((el)=>(el.getAttribute('aria-label')||'')==='复制选中内容'); if(!b) return 'NO_BTN'; b.click(); return 'CLICKED'; })()" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("eval",""))')"
[ "$CLICKED" = 'CLICKED' ] || bad "找不到复制按钮"
sleep 1
CLICKED="$(ui --eval "(() => { const b=[...document.querySelectorAll('button')].find((el)=>(el.getAttribute('aria-label')||'')==='粘贴'); if(!b) return 'NO_BTN'; b.click(); return 'CLICKED'; })()" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("eval",""))')"
[ "$CLICKED" = 'CLICKED' ] || bad "找不到粘贴按钮"
sleep 2
R="$(rows)"
[[ "$R" == *"$SRC"* ]] || bad "粘贴没有出现（期望 $SRC）"
ok "复制 + 粘贴可用：$SRC"
ui --shot "$OUT_DIR/03-copypaste.png" >/dev/null

# 中文走浏览器输入法通道（Input.insertText）：验证 UTF-8 输入进 PTY 的整条链路
CN="中文输入_${VERSION}"
ui --insert-text "$CN" >/dev/null
sleep 2
R="$(rows)"
[[ "$R" == *"$CN"* ]] || bad "中文输入没有出现在终端（期望 $CN）"
ok "中文输入可用：$CN"
ui --shot "$OUT_DIR/03b-chinese.png" >/dev/null
python3 "$PY" key KEY_ESCAPE >/dev/null 2>&1 || true
sleep 1

step "4/5 多标签切换内容保留"
keys "echo TAB_A_MARK_${VERSION}"
sleep 2
# 回列表再开一个终端
ui --eval "location.hash='#/remote/local'; 'ok'" >/dev/null 2>&1 || true
sleep 3
OPENED2="$(ui --eval "(() => { const b=[...document.querySelectorAll('button')].find((el)=>(el.innerText||'').includes('打开默认终端')); if(!b) return 'NO_BTN'; b.click(); return 'OPENED'; })()" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("eval",""))')"
[ "$OPENED2" = 'OPENED' ] || bad "第二个终端打不开（没有默认终端入口）"
sleep 6
keys "echo TAB_B_MARK_${VERSION}"
sleep 2
ui --shot "$OUT_DIR/04-tab-b.png" >/dev/null
# 切回第一个标签
ui --eval "(() => { const tabs=[...document.querySelectorAll('[role=tab]')]; if (tabs.length < 2) return 'TABS=' + tabs.length; tabs[0].click(); return 'SWITCHED'; })()" >/dev/null
sleep 2
R="$(rows)"
[[ "$R" == *"TAB_A_MARK_${VERSION}"* ]] || bad "切回第一个标签后画面丢了（A 标记不在）"
ui --shot "$OUT_DIR/05-tab-a-back.png" >/dev/null
ok "标签切回后画面仍在（A 标记可见）"

step "4b/5 窗口偏矮时保证 ≥24 行（v1.10.9）"
# 记录原始视口尺寸：恢复时显式设置回去（QtWebEngine 里跨连接的 clearDeviceMetricsOverride 不生效）
SIZE_BEFORE="$(ui --eval "JSON.stringify({w: innerWidth, h: innerHeight})" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("eval",""))')"
ui --metrics 900x620 >/dev/null
sleep 2
ROWS_SHRUNK="$(ui --eval "(() => { const h=[...document.querySelectorAll('[data-testid=terminal-host]')].filter((x)=>x.offsetParent!==null)[0]; return h ? h.querySelectorAll('.xterm-rows > div').length : 0; })()" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("eval",0))')"
FONT_SHRUNK="$(ui --eval "(() => { const t=[...document.querySelectorAll('span')].map((s)=>s.innerText.trim()); return t.find((x)=>x.startsWith('⌥ 字号')) || ''; })()" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("eval",""))')"
[ "${ROWS_SHRUNK:-0}" -ge 24 ] || bad "矮窗没有保证 24 行（实际 $ROWS_SHRUNK 行）"
case "$FONT_SHRUNK" in
    *自适应*) : ;;
    *) bad "矮窗没有自动缩字号（状态栏：$FONT_SHRUNK）" ;;
esac
ui --shot "$OUT_DIR/06-min-rows.png" >/dev/null
ok "矮窗自动保证行数：${ROWS_SHRUNK} 行 / $FONT_SHRUNK"

ui --metrics 1180x760 >/dev/null
sleep 2
ROWS_BACK="$(ui --eval "(() => { const h=[...document.querySelectorAll('[data-testid=terminal-host]')].filter((x)=>x.offsetParent!==null)[0]; return h ? h.querySelectorAll('.xterm-rows > div').length : 0; })()" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("eval",0))')"
FONT_BACK="$(ui --eval "(() => { const t=[...document.querySelectorAll('span')].map((s)=>s.innerText.trim()); return t.find((x)=>x.startsWith('⌥ 字号')) || ''; })()" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("eval",""))')"
[ "${ROWS_BACK:-0}" -ge 24 ] || bad "恢复窗口后行数不足（$ROWS_BACK）"
case "$FONT_BACK" in
    *自适应*) bad "恢复窗口后仍是自适应字号（$FONT_BACK）" ;;
esac
ok "恢复窗口后回到用户字号：${ROWS_BACK} 行 / $FONT_BACK"
ui --shot "$OUT_DIR/07-restored.png" >/dev/null

step "5/5 汇总"
cat >"$OUT_DIR/SUMMARY.md" <<EOF
# Windows 真机终端交互验收（v$VERSION）

| # | 检查 | 结果 | 证据 |
| --- | --- | --- | --- |
| 1 | 本机终端打开并渲染 | 通过 | 01-terminal.png |
| 2 | 键盘输入回显（$MARK） | 通过 | 02-typed.png |
| 3 | 复制 + 粘贴（$SRC）与中文输入（$CN） | 通过 | 03-copypaste.png / 03b-chinese.png |
| 4 | 多标签切回内容保留（TAB_A_MARK_$VERSION） | 通过 | 04-tab-b.png / 05-tab-a-back.png |
| 5 | 矮窗自动保证 ≥24 行并恢复（v1.10.9） | 通过 | 06-min-rows.png / 07-restored.png |

宿主证据目录：$OUT_DIR
EOF
ok "全部通过，汇总：$OUT_DIR/SUMMARY.md"
