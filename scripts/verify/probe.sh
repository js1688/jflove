#!/usr/bin/env bash
# 验证入口统一探测（先跑它，再判“能否跳过”）
#
# 铁律（来自实际事故，不是理论）：
#   1. 禁止用 `2>/dev/null` 把「连不上守护进程」吞成「域不存在」——
#      两者处置完全不同：前者可自启，后者才需要重建靶机。
#   2. 禁止用默认命令/默认 URI 直接判缺失；system 与 session 两个 URI 都要查。
#   3. 禁止把「没启动」判成「没有」：AVD 存在但模拟器未起 ≠ 不能测。
#   4. 探测只描述事实，不替调用方决定“跳过”；能否跳过由 run_* 脚本按字段判断。
#
# 输出 JSON（退出码 0；每种状态都是**不同**的字符串，绝不混淆）：
#   windows_vm:        running | shut off | paused | daemon-down | domain-missing
#   windows_vm_session:同 system（session 模式无需 root，是重要兜底通道）
#   windows_vm_rootless: yes/no  —— 本机是否具备无 root 起 VM 的能力（/dev/kvm + session URI）
#   libvirt_daemon:    active | inactive | absent
#   libvirt_hint:      人类可读的修复建议（daemon-down 时非空）
#   linux_display:     x11::N | wayland | missing
#   linux_wayland:     wayland-N | ""
#   linux_ui:          x11 | wayland | offscreen+cdp | none
#   xvnc / ydotool / xinput: installed | missing
#   android:           device | avd-not-started | no-avd | missing(sdk)
#   android_avd:       AVD 名 | none
#   android_hint:      可执行的启动命令（avd-not-started 时非空）
#   offscreen_cdp:     yes | no
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VM="${JF_WIN_VM:-jflove-win10}"
UID_NUM="$(id -u)"
ANDROID_HOME="${ANDROID_HOME:-$HOME/sdk/android-sdk}"

# ── libvirt 守护进程状态 ────────────────────────────────────────
# 分三层查：systemd 单元 → 进程 → socket 可连性。任一层能通就算 active。
libvirt_daemon="absent"
if command -v systemctl >/dev/null 2>&1; then
    for unit in virtqemud.service virtqemud.socket libvirtd.service libvirtd.socket; do
        st="$(systemctl is-active "$unit" 2>/dev/null || true)"
        if [ "$st" = "active" ]; then libvirt_daemon="active"; break; fi
        [ "$st" = "inactive" ] && libvirt_daemon="inactive"
    done
fi
# 进程兜底：systemd 说 inactive 但进程在（如手动起过），仍算 active
if [ "$libvirt_daemon" != "active" ] && pgrep -u "$UID_NUM" -x virtqemud >/dev/null 2>&1; then
    libvirt_daemon="active"
fi

# ── 域状态查询：区分「守护进程挂」与「域不存在」────────────────
# 关键：不要 2>/dev/null。先判断连接是否成功，再判断域是否存在。
# 返回 running|shut off|paused|daemon-down|domain-missing
dom_state_safe() {
    local uri="$1" err out
    err="$(mktemp)"; out="$(mktemp)"
    if virsh -c "$uri" domstate "$VM" >"$out" 2>"$err"; then
        tr -d '[:space:]' <"$out"
        rm -f "$out" "$err"; return 0
    fi
    # 失败：看错误文本决定是「连不上守护进程」还是「域不存在」。
    # 顺序很重要：先匹配"域不存在"（这是确定性结论），其余一律按"连不上"处理。
    local e; e="$(cat "$err" 2>/dev/null)"
    rm -f "$out" "$err"
    case "$e" in
        # 域不存在的各种表述（含 session URI 下的「无法获得域」）
        *"failed to get domain"*|*"Domain not found"*|*"无法获得域"*|*"域未找到"*|*"未找到域"*|*"no domain with"*)
            echo "domain-missing" ;;
        # 连不上守护进程
        *"无法连接到 hypervisor"*|*"Failed to connect"*|*"没有那个文件或目录"*|*"Connection refused"*|*"No such file"*)
            echo "daemon-down" ;;
        # 未知错误绝不谎报 missing —— 明确标注，逼调用方看原文
        *) echo "unknown-error" ;;
    esac
}

vm_state="$(dom_state_safe qemu:///system)"
vm_session="$(dom_state_safe qemu:///session)"

# 若 system 报 daemon-down，但域实际定义在 system 且进程存在，仍按真实原因报
# （不把 daemon-down 美化成 missing —— 这正是历史事故的根因）

libvirt_hint=""
# 只有「两个 URI 都连不上」才提示守护进程问题；
# 任一 URI 能连上（哪怕域不在）就说明守护进程是活的，不误导用户去 systemctl。
if [ "$vm_state" = "daemon-down" ] && [ "$vm_session" = "daemon-down" ]; then
    libvirt_hint="libvirt 守护进程不可用（system 与 session 均连不上）。system 模式需 sudo：sudo systemctl enable --now virtqemud.socket；无 root 时可改用 session 模式（本机 windows_vm_rootless=yes 时可直接起）。域与磁盘可能完好，勿重建靶机。"
elif [ "$vm_state" = "daemon-down" ] && [ "$vm_session" != "daemon-down" ]; then
    libvirt_hint="session 通道可用但 system 通道不可用（system 守护进程未起）。直接跑 scripts/verify/vm-win.sh up 即可（脚本会自动走 session 通道，无需 root）；如需 system 模式再执行 sudo systemctl enable --now virtqemud.socket。"
fi
if [ "$vm_state" = "domain-missing" ] && [ "$vm_session" = "domain-missing" ]; then
    libvirt_hint="两个 URI 均无域 ${VM}。若磁盘仍在（/var/lib/libvirt/images/${VM}.qcow2），只需重新 define，勿重建。"
fi

# ── 无 root 能力判定：/dev/kvm 可写 + session URI 可用 ──────────
windows_vm_rootless="no"
if [ -r /dev/kvm ] && [ -w /dev/kvm ]; then
    if virsh -c qemu:///session list --all >/dev/null 2>&1; then
        windows_vm_rootless="yes"
    fi
fi

# ── 统一就绪判定：任一通道能管这台 VM 就算「具备」 ──────────────
# 这是本文件最重要的输出：调用方只看 windows_vm_ready，不要再自己解释 system/session。
# 语义（严格区分，别混）：
#   yes        = 域正在运行且 QGA 就绪，可直接做验证
#   starting   = 域在运行但 QGA 未就绪（开机中），稍等即可
#   recoverable= 域已定义但关机 / 域未定义但磁盘在 → 一条命令可拉起，**不是“不具备”**
#   no         = 无域、无磁盘、无可用通道 → 真没有
windows_vm_ready="no"; windows_vm_channel=""; windows_vm_qga="no"; windows_vm_state=""
for _uri in qemu:///system qemu:///session; do
    _st="$(virsh -c "$_uri" domstate "$VM" 2>/dev/null | head -1 | tr -d '[:space:]' || true)"
    [ -z "$_st" ] && continue
    windows_vm_channel="$_uri"
    windows_vm_state="$_st"
    case "$_st" in
        运行|running)
            if virsh -c "$_uri" qemu-agent-command "$VM" '{"execute":"guest-ping"}' >/dev/null 2>&1; then
                windows_vm_qga="yes"; windows_vm_ready="yes"
            else
                windows_vm_ready="starting"
            fi
            ;;
        *) windows_vm_ready="recoverable" ;;   # 关机/暂停：能 start
    esac
    break
done
# 域都查不到（未定义）：磁盘在 + 通道可用 ⇒ 可自恢复，不是“没有靶机”
if [ "$windows_vm_ready" = "no" ]; then
    _disk="${JF_WIN_DISK:-$HOME/vms/win10/${VM}.qcow2}"
    _src="/var/lib/libvirt/images/${VM}.qcow2"
    if [ -f "$_disk" ] || [ -f "$_src" ]; then
        if [ "$windows_vm_rootless" = "yes" ] || virsh -c qemu:///system list --all >/dev/null 2>&1; then
            windows_vm_ready="recoverable"
        fi
    fi
fi

# ── Linux 显示 ─────────────────────────────────────────────────
linux_display="missing"; linux_wayland=""
if [ -n "${DISPLAY:-}" ] && xdpyinfo >/dev/null 2>&1; then
    linux_display="x11:${DISPLAY}"
fi
for d in :0 :1 :99; do
    [ "$linux_display" = "missing" ] || break
    if DISPLAY="$d" xdpyinfo >/dev/null 2>&1; then linux_display="x11:${d}"; fi
done
if ls /run/user/"$UID_NUM"/wayland-* >/dev/null 2>&1; then
    linux_wayland="$(basename "$(ls /run/user/"$UID_NUM"/wayland-* 2>/dev/null | grep -v '\.lock$' | head -1)")"
    [ "$linux_display" = "missing" ] && linux_display="wayland"
fi

linux_ui="none"
if [ "$linux_display" != "missing" ]; then
    linux_ui="x11"
    [ "$linux_display" = "wayland" ] && linux_ui="wayland"
elif [ -x "$ROOT/jflove-desktop/venv-linux/bin/python" ] && [ -f "$ROOT/scripts/verify/desktop_shot.py" ]; then
    linux_ui="offscreen+cdp"
fi

xvnc="missing";  command -v Xvnc  >/dev/null 2>&1 && xvnc="installed"
ydotool="missing"; command -v ydotool >/dev/null 2>&1 && ydotool="installed"
xinput="missing"; command -v xinput >/dev/null 2>&1 && xinput="installed"

# ── Android：区分「没 SDK」「没 AVD」「AVD 在但没启动」「已连接」──
android_avd="none"
android="missing"
android_hint=""
emu="$ANDROID_HOME/emulator/emulator"
adb_bin="$ANDROID_HOME/platform-tools/adb"
if [ -x "$emu" ]; then
    android_avd="$("$emu" -list-avds 2>/dev/null | head -1)"
    [ -z "$android_avd" ] && android_avd="none"
    android="no-avd"
    if [ -x "$adb_bin" ]; then
        if "$adb_bin" devices 2>/dev/null | grep -q 'device$'; then
            android="device"
        elif [ "$android_avd" != "none" ]; then
            # 关键修正：有 AVD 只是没启动，不等于不能测
            android="avd-not-started"
            android_hint="模拟器未启动。执行：bash scripts/verify/android_emulator.sh start（内部会自动配 Xvnc + -gpu host）"
        fi
    fi
fi

offscreen_cdp="no"
if [ -x "$ROOT/jflove-desktop/venv-linux/bin/python" ] && [ -f "$ROOT/scripts/verify/desktop_shot.py" ]; then
    offscreen_cdp="yes"
fi

python3 - "$vm_state" "$linux_display" "$linux_wayland" "$linux_ui" "$xvnc" \
         "$ydotool" "$xinput" "$android" "$android_avd" "$offscreen_cdp" \
         "$vm_session" "$libvirt_daemon" "$libvirt_hint" "$android_hint" "$windows_vm_rootless" \
         "$windows_vm_ready" "$windows_vm_channel" "$windows_vm_qga" "$windows_vm_state" <<'PY'
import json, sys
print(json.dumps({
    # ★ 首选：只看这个。yes=可跑；recoverable=磁盘在、能自动拉起；no=真没有
    "windows_vm_ready":    sys.argv[16],
    "windows_vm_channel":  sys.argv[17],
    "windows_vm_qga":      sys.argv[18],
    "windows_vm_state":    sys.argv[19],
    "windows_vm_state":    sys.argv[19],
    "windows_vm":          sys.argv[1],
    "windows_vm_session":  sys.argv[11],
    "windows_vm_rootless": sys.argv[15],
    "libvirt_daemon":      sys.argv[12],
    "libvirt_hint":        sys.argv[13],
    "linux_display":       sys.argv[2],
    "linux_wayland":       sys.argv[3],
    "linux_ui":            sys.argv[4],
    "xvnc":                sys.argv[5],
    "ydotool":             sys.argv[6],
    "xinput":              sys.argv[7],
    "android":             sys.argv[8],
    "android_avd":         sys.argv[9],
    "android_hint":        sys.argv[14],
    "offscreen_cdp":       sys.argv[10],
}, ensure_ascii=False, indent=2))
PY
