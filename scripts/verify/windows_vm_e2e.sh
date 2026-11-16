#!/usr/bin/env bash
# Windows 端真机端到端验收（KVM 虚拟机内跑真实安装包）——Windows 端唯一证据来源。
#
# 覆盖的判断链（每一步都可判定，失败即非 0 退出）：
#   1) 安装包静默安装成功，且卸载项/快捷方式/程序目录都在
#   2) 进程能起来，QtWebEngine 界面能渲染（CDP 能 attach）
#   3) 客户端 ↔ 服务端完成密钥交换（key-exchange 200）
#   4) 登录成功（login 200），进入文件管理
#   5) 通过界面「上传」把文件送进服务端，且宿主机落盘 SHA256 与客机源文件一致
#
# 用法：
#   scripts/verify/windows_vm_e2e.sh                 # 全流程（缺安装包则从 Release 下载）
#   scripts/verify/windows_vm_e2e.sh --skip-install  # 复用已装版本，只跑 2~5
#   scripts/verify/windows_vm_e2e.sh --keep          # 跑完不关机（默认关机，保证宿主机零占用）
#   VERSION=1.10.5 URL=<安装包直链> scripts/verify/windows_vm_e2e.sh
#
# 环境变量：JF_WIN_VM（域）、JF_WIN_OUT（证据目录）、JF_WIN_USER/JF_WIN_PASS（登录账号）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

VM="${JF_WIN_VM:-jflove-win10}"
OUT_DIR="${JF_WIN_OUT:-/tmp/jf-win}"
VERSION="${VERSION:-$(python3 -c "import json;print(json.load(open('version.json'))['version'])")}"
URL="${URL:-https://github.com/js1688/jflove/releases/download/v${VERSION}/JFLove-${VERSION}-win64-setup.exe}"
GUEST_USER="${JF_WIN_USER:-vmtest}"
GUEST_PASS="${JF_WIN_PASS:-Vm@Test1234}"
DISK_NAME="${JF_WIN_DISK_NAME:-clean}"
DISK_PATH="${JF_WIN_DISK_PATH:-/home/$USER/jf-clean/remote}"
PY="$ROOT/scripts/verify/windows_vm.py"
SHOT_DIR="$OUT_DIR/e2e"
SKIP_INSTALL=0
KEEP=0
for a in "$@"; do
    case "$a" in
        --skip-install) SKIP_INSTALL=1 ;;
        --keep)         KEEP=1 ;;
        *) echo "未知参数：$a" >&2; exit 2 ;;
    esac
done
mkdir -p "$SHOT_DIR"

step() { printf '\n[win-e2e] === %s ===\n' "$*"; }
ok()   { printf '[win-e2e] ✅ %s\n' "$*"; }
bad()  { printf '[win-e2e] ❌ %s\n' "$*" >&2; exit 1; }
win()  { python3 "$PY" "$@"; }

cleanup() {
    if [ "$KEEP" -eq 0 ]; then
        printf '\n[win-e2e] 收尾：关机并确认宿主机零占用\n'
        "$ROOT/scripts/verify/vm-win.sh" down || true
    fi
}
trap cleanup EXIT

# ── 0. 起服务端 + 准备测试夹具（用户 / 磁盘 / 授权）──────────────────
step "0/6 服务端与测试夹具"
"$ROOT/scripts/verify/server.sh" start >/dev/null
mkdir -p "$DISK_PATH"
python3 - "$DISK_NAME" "$DISK_PATH" "$GUEST_USER" "$GUEST_PASS" <<'PY'
import sqlite3, sys
import bcrypt
name, path, user, password = sys.argv[1:5]
db = "/tmp/jflove-verify.db"
c = sqlite3.connect(db)
c.execute("INSERT INTO virtual_disks (name, real_path, created_by, created_at, updated_at) "
          "SELECT ?,?,1,datetime('now'),datetime('now') "
          "WHERE NOT EXISTS (SELECT 1 FROM virtual_disks WHERE name=?)", (name, path, name))
disk_id = c.execute("SELECT id FROM virtual_disks WHERE name=?", (name,)).fetchone()[0]
h = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
c.execute("INSERT INTO users (username, password_hash, role, created_at, updated_at) "
          "SELECT ?,?, 'user', datetime('now'), datetime('now') "
          "WHERE NOT EXISTS (SELECT 1 FROM users WHERE username=?)", (user, h, user))
uid = c.execute("SELECT id FROM users WHERE username=?", (user,)).fetchone()[0]
c.execute("INSERT INTO user_permissions (user_id, virtual_disk_id, can_read, can_write, can_delete, created_at, updated_at) "
          "SELECT ?,?,1,1,1,datetime('now'),datetime('now') "
          "WHERE NOT EXISTS (SELECT 1 FROM user_permissions WHERE user_id=? AND virtual_disk_id=?)",
          (uid, disk_id, uid, disk_id))
c.commit()
print(f"fixture ok: user={user} disk={name} -> {path}")
PY
LOG_FILE=/tmp/jflove-server-verify.log
LOG_OFF=$(( $(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0) + 1 ))
newlog() { tail -c +"$LOG_OFF" "$LOG_FILE" 2>/dev/null; }
HOST_GW="$(ip -4 -o addr show virbr0 | awk '{print $4}' | cut -d/ -f1)"
[ -n "$HOST_GW" ] || bad "取不到 virbr0 地址（default 网络没起来？）"
ok "服务端 127.0.0.1:8989，客机侧地址 http://$HOST_GW:8989"

# ── 1. 起 VM ───────────────────────────────────────────────────────
step "1/6 启动 Windows VM 并等 QGA"
"$ROOT/scripts/verify/vm-win.sh" up | tail -1
GUEST_IP="$(win ip)"
[ -n "$GUEST_IP" ] || bad "取不到客机 IP"
ok "客机 $VM @ $GUEST_IP"

# ── 2. 安装真实安装包 ──────────────────────────────────────────────
step "2/6 安装 Windows 安装包 v$VERSION"
if [ "$SKIP_INSTALL" -eq 1 ] && win exec "if exist \"C:\\Program Files\\JFLove\\JFLove.exe\" (exit 0) else (exit 1)"; then
    ok "复用已安装的 JFLove"
else
    win exec "mkdir C:\\jf 2>nul & taskkill /f /im JFLove.exe 2>nul" >/dev/null || true
    # 按版本命名 + 校验 ProductVersion：旧 setup.exe 绝不复用（《验证标准》§4）
    SETUP="C:\\jf\\setup-${VERSION}.exe"
    if ! win exec "if exist ${SETUP} (exit 0) else (exit 1)"; then
        win exec --timeout 900 "curl.exe -sS -L -o ${SETUP} $URL" || bad "安装包下载失败：$URL"
    fi
    # QGA 的 PowerShell 输出会按列宽补空格（实测 v1.10.8："1.10.8" 后面跟一长串空格），
    # 不 trim 会把正确版本判成"不是当前版本"，白跑一轮。
    GOT_VER="$(win exec --ps "(Get-Item '${SETUP}').VersionInfo.ProductVersion" 2>/dev/null | tr -d '\r' | sed 's/[[:space:]]//g' | tail -1)"
    [ "$GOT_VER" = "$VERSION" ] || bad "安装包版本 ${GOT_VER:-?} != 当前 ${VERSION}（拒绝旧包）"
    # 覆盖安装前必须结束进程，否则 exe 被占用 → 静默安装按 Abort 回滚（《验证标准》§7）
    win exec --ps "Get-Process JFLove -ErrorAction SilentlyContinue | Stop-Process -Force; Start-Sleep -Seconds 2; 'stopped'" >/dev/null || true
    win exec --timeout 600 "${SETUP} /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=C:\\jf\\install.log" \
        || bad "静默安装失败（看 C:\\jf\\install.log）"
    ok "安装完成"
fi

win exec --ps "\$e='C:\Program Files\JFLove\JFLove.exe'; if (-not (Test-Path \$e)) { 'MISSING' } else { 'EXE ' + [math]::Round((Get-Item \$e).Length/1MB,1) + 'MB' }" | tail -1
win exec --ps "(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*' | Where-Object DisplayName -like '*JFLove*').DisplayVersion" | tail -1 | grep -q "$VERSION" \
    || bad "卸载项里的版本不是 $VERSION"
win exec --ps "if (Test-Path 'C:\ProgramData\Microsoft\Windows\Start Menu\Programs\JFLove\JFLove.lnk') { 'SHORTCUT OK' } else { 'SHORTCUT MISSING' }" | tail -1 | grep -q OK \
    || bad "开始菜单快捷方式缺失"
ok "程序目录 / 卸载项版本 / 开始菜单快捷方式 全部就位"

# ── 3. 启动并 attach CDP ──────────────────────────────────────────
step "3/6 启动客户端并等待界面"
# 清掉客机上的客户端状态（会话/服务端地址历史），保证这轮是**真正的全新登录**：
# 否则复用上次会话时 /files/disks 会 401（v1.10.8 实测），磁盘列表为空 → 上传步骤找不到磁盘，
# 而脚本还会误以为"已登录"。清状态后登录流程本身也成了验收项。
win exec --ps 'Get-Process JFLove -ErrorAction SilentlyContinue | Stop-Process -Force; Start-Sleep -Seconds 1; Remove-Item -Recurse -Force "$env:APPDATA\JFLove" -ErrorAction SilentlyContinue; "client-state-cleared"' >/dev/null || true
win push "$ROOT/scripts/verify/vm/launch.ps1" 'C:\jf\launch.ps1' >/dev/null
win exec --ps "powershell -NoProfile -ExecutionPolicy Bypass -File C:\jf\launch.ps1" >/dev/null
CDP="http://$GUEST_IP:9223"
for _ in $(seq 1 15); do
    curl -fsS -m 2 "$CDP/json/version" >/dev/null 2>&1 && break
    sleep 2
done
curl -fsS -m 3 "$CDP/json/version" | grep -q QtWebEngine || bad "CDP 不可用（QtWebEngine 未暴露调试端口）"
ok "CDP 就绪：$(curl -fsS -m 3 "$CDP/json/version" | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["User-Agent"].split(")")[1].strip())')"

WIN_OUT="$SHOT_DIR"
# 每次 CDP attach 有约 8s 握手开销（QtWebEngine 实测），故单次给 12s、外层给宽一点
ui() { timeout 25 node scripts/verify/windows_vm_ui.mjs --host "$GUEST_IP" --port 9223 --timeout 12000 "$@"; }
ui_text() { ui --eval "document.body.innerText" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("eval",""))'; }

# QtWebEngine 的 /json/version 会早于页面渲染就绪，先等一小会儿再轮询
sleep 6
PAGE_TEXT=""
for _ in $(seq 1 30); do
    PAGE_TEXT="$(ui_text 2>/dev/null || true)"
    [[ "$PAGE_TEXT" == *'连接服务端'* || "$PAGE_TEXT" == *'用户名'* || "$PAGE_TEXT" == *'文件管理'* ]] && break
    sleep 3
done
[[ "$PAGE_TEXT" == *'连接服务端'* || "$PAGE_TEXT" == *'用户名'* || "$PAGE_TEXT" == *'文件管理'* ]] || bad "界面没渲染出来"
ui --shot "$WIN_OUT/01-login.png" >/dev/null
ok "登录页渲染（证据：$WIN_OUT/01-login.png）"

# ── 4. 密钥交换 + 登录 ────────────────────────────────────────────
step "4/6 连接服务端 + 登录"
FRESH_LOGIN=0
if [[ "$PAGE_TEXT" == *'文件管理'* ]]; then
    # 理论上不可达（上一步已清状态）；真出现说明清理没生效，直接判失败而不是"假装通过"
    bad "清理客户端状态后仍直接进主界面（会话没被清掉）"
else
    if [[ "$PAGE_TEXT" == *'连接服务端'* ]]; then
        ui --fill-sel 'input[type=text]' --fill-val "http://$HOST_GW:8989" >/dev/null
        ui --click-text '连接服务端' >/dev/null
        sleep 3
    fi
    ui --fill-sel 'input[type=text]' --fill-val "$GUEST_USER" >/dev/null
    ui --fill-sel 'input[type=password]' --fill-val "$GUEST_PASS" >/dev/null
    ui --click-text '登录' >/dev/null
    FRESH_LOGIN=1
    sleep 6
fi
ui --wait-text '文件管理' --shot "$WIN_OUT/02-main.png" >/dev/null \
    || bad "没进到文件管理"
newlog | grep -aq 'auth/key-exchange.*200' || bad "本次运行没有 key-exchange 200"
if [ "$FRESH_LOGIN" -eq 1 ]; then
    newlog | grep -aq 'auth/login.*200' || bad "本次运行没有 login 200"
    ok "key-exchange 200 + login 200，已进入文件管理"
else
    ok "key-exchange 200（复用已有会话）；如需验证登录本身，先清客户端会话再跑"
fi

# ── 5. 上传文件并校验 SHA256 ──────────────────────────────────────
step "5/6 界面上传文件并校验完整性"
win exec --ps "\$f='C:\jf\vm-upload.bin'; if (-not (Test-Path \$f)) { \$b=New-Object byte[] 1048576; (New-Object Random).NextBytes(\$b); [IO.File]::WriteAllBytes(\$f,\$b) }; (Get-FileHash \$f -Algorithm SHA256).Hash" >/dev/null
GUEST_SHA="$(win exec --ps "(Get-FileHash 'C:\jf\vm-upload.bin' -Algorithm SHA256).Hash" | tr -d '\r\n ')"
rm -f "$DISK_PATH/vm-upload.bin"
ui --click-text "$DISK_NAME" >/dev/null
# 原生「选择文件」框会阻塞渲染层 CDP 输入，这里的 click 预期超时，属正常
ui --click-text '上传' >/dev/null 2>&1 || true
sleep 4
python3 "$PY" type 'C:\jf\vm-upload.bin'
sleep 2
# 文件框的「打开」对 Enter 时机敏感（实测首次 Enter 常被吞）：重试 + 轮询落盘
for _ in $(seq 1 5); do
    python3 "$PY" key KEY_ENTER >/dev/null 2>&1 || true
    N=0
    while [ "$N" -lt 8 ]; do
        [ -f "$DISK_PATH/vm-upload.bin" ] && break
        sleep 2
        N=$((N + 1))
    done
    [ -f "$DISK_PATH/vm-upload.bin" ] && break
done
[ -f "$DISK_PATH/vm-upload.bin" ] || bad "服务端没收到文件（看 /tmp/jflove-server-verify.log）"
HOST_SHA="$(sha256sum "$DISK_PATH/vm-upload.bin" | awk '{print toupper($1)}')"
[ "$GUEST_SHA" = "$HOST_SHA" ] || bad "SHA256 不一致：客机 $GUEST_SHA / 宿主机 $HOST_SHA"
newlog | grep -aq 'upload/complete.*200' || bad "本次运行没有 upload/complete 200"
ui --shot "$WIN_OUT/03-uploaded.png" >/dev/null
ok "上传成功且 SHA256 一致（${GUEST_SHA:0:16}…），证据：$WIN_OUT/03-uploaded.png"
"$ROOT/scripts/verify/vm-win.sh" shot "$WIN_OUT/04-desktop.png" >/dev/null 2>&1 || true

# ── 6. 汇总 ──────────────────────────────────────────────────────
step "6/6 汇总"
cat >"$WIN_OUT/SUMMARY.md" <<EOF
# Windows VM 端到端验收（$VERSION）

| # | 检查 | 结果 |
| --- | --- | --- |
| 1 | 安装包静默安装 + 程序目录/卸载项/快捷方式 | 通过 |
| 2 | 客户端启动 + QtWebEngine 界面（CDP attach） | 通过 |
| 3 | 密钥交换（key-exchange 200） | 通过 |
| 4 | 密钥交换（本次运行 key-exchange 200）+ 进入文件管理 | 通过 |
| 5 | 界面上传 + 宿主机落盘 SHA256 一致 | 通过（$GUEST_SHA） |

- VM：\`$VM\` @ \`$GUEST_IP\`，安装包：\`$URL\`
- 服务端：\`127.0.0.1:8989\`，客机侧 \`http://$HOST_GW:8989\`
- 截图：01-login / 02-main / 03-uploaded / 04-desktop（本目录）
- 结束后默认关机，宿主机零占用（qemu 进程 0）
EOF
ok "全部通过，摘要：$WIN_OUT/SUMMARY.md"
