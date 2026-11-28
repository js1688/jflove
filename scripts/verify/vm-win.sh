#!/usr/bin/env bash
# Windows 10 真机验证入口（KVM/libvirt 虚拟机）——Windows 端一律走本脚本。
#
# 为什么要有它：
#   模拟层覆盖不到 Inno 安装器、文件关联、Defender/SmartScreen、媒体解码与真实 Win32 行为。
#
# ★ 通道自动解析（本脚本最重要的一点）：
#   system 模式需要 root 启守护进程；本机 sudo 要密码。若 system 不可用，本脚本
#   **自动切到 session 模式**（qemu:///session，无需 root），并自动从原盘 reflink 出
#   用户副本、自动 define 域。**不要因为 system 连不上就说"没有靶机"**。
#   细节见 scripts/verify/libvirt_env.sh。
#
# 用法：
#   scripts/verify/vm-win.sh probe              # 自检：哪条通道可用、靶机是否就绪
#   scripts/verify/vm-win.sh up                 # 起 VM + 等 QGA（自动选通道/备盘/define）
#   scripts/verify/vm-win.sh down               # ACPI 关机 → 兜底 destroy
#   scripts/verify/vm-win.sh status             # 状态 / IP / QGA / 磁盘
#   scripts/verify/vm-win.sh shot [out]         # 截屏（默认 /tmp/jf-win/shot.png）
#   scripts/verify/vm-win.sh net up|down        # 断网注入（仅 system 模式支持 virsh net-*）
#   scripts/verify/vm-win.sh snap [name]        # 建磁盘快照（默认 clean）
#   scripts/verify/vm-win.sh revert [name]      # 回滚到快照（先关机）
#   scripts/verify/vm-win.sh footprint          # 实测「停掉后不占 CPU/内存」
#   scripts/verify/vm-win.sh uri                # 打印解析出的 libvirt URI（供其他脚本消费）
#
# 客机内执行 / 传文件走 python3 scripts/verify/windows_vm.py（QGA 通道，自动读同一 URI）。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/scripts/verify/libvirt_env.sh"

VM="${JF_LIB_VM}"
SNAP="${JF_WIN_SNAP:-clean}"
OUT_DIR="${JF_WIN_OUT:-/tmp/jf-win}"
PY="$ROOT/scripts/verify/windows_vm.py"

mkdir -p "$OUT_DIR"

say() { printf '[vm-win] %s\n' "$*"; }
die() { printf '[vm-win] 失败：%s\n' "$*" >&2; exit 1; }

proc_pattern() { echo "$VM"; }
dom_state() { jf_vsh domstate "$VM" 2>/dev/null | head -1 | tr -d '[:space:]' || true; }

wait_agent() {
    local timeout="${1:-300}" i
    for ((i = 0; i < timeout / 3; i++)); do
        if jf_vsh qemu-agent-command "$VM" '{"execute":"guest-ping"}' >/dev/null 2>&1; then
            return 0
        fi
        sleep 3
    done
    return 1
}

cmd_probe() {
    local rc=0
    jf_select_uri || rc=$?
    echo "通道      : ${JF_LIBVIRT_URI:-不可用} (${JF_WIN_MODE:-none})"
    echo "原盘      : $JF_SRC_DISK ($( [ -f "$JF_SRC_DISK" ] && du -h "$JF_SRC_DISK" | cut -f1 || echo 缺失))"
    echo "用户副本  : $JF_SESS_DISK ($( [ -f "$JF_SESS_DISK" ] && du -h "$JF_SESS_DISK" | cut -f1 || echo 未创建))"
    if [ "$rc" -eq 0 ]; then
        local st; st="$(dom_state)"
        echo "域        : ${st:-未定义（up 时会自动 define）}"
        if jf_vsh qemu-agent-command "$VM" '{"execute":"guest-ping"}' >/dev/null 2>&1; then
            echo "QGA       : 就绪 ✓"
        else
            echo "QGA       : 未就绪"
        fi
        say "结论：Windows 靶机通道可用"
        return 0
    fi
    return 3
}

cmd_up() {
    jf_prepare_windows || die "准备 Windows 靶机失败（通道或磁盘不可用）"

    case "$(dom_state)" in
        运行|running) say "已在运行（$JF_WIN_MODE）" ;;
        "")
            jf_prepare_windows || die "域未定义且自动 define 失败"
            say "启动 $VM（$JF_WIN_MODE）"
            jf_vsh start "$VM" >/dev/null 2>&1 || die "start 失败"
            ;;
        *)
            say "启动 $VM（$JF_WIN_MODE）"
            jf_vsh start "$VM" >/dev/null 2>&1 || die "start 失败"
            ;;
    esac

    # system 模式下确保 default 网络；session 用 user 网络，无需 net-*
    if [ "$JF_WIN_MODE" = "system" ]; then
        jf_vsh net-start default >/dev/null 2>&1 || true
    fi

    say "等待 QGA（最多 5 分钟）…"
    if wait_agent 300; then say "QGA 就绪 ✓"; else say "QGA 未就绪（可能仍在开机），可稍后 status 重试"; fi
    cmd_status
    return 0
}

cmd_down() {
    case "$(dom_state)" in
        "" ) say "域不存在，无需停止"; return 0 ;;
        运行|running|paused|挂起) ;;
        *) say "当前状态：$(dom_state)，无需停止"; return 0 ;;
    esac
    say "ACPI 关机…"
    jf_vsh shutdown "$VM" >/dev/null 2>&1 || true
    local i
    for ((i = 0; i < 40; i++)); do
        case "$(dom_state)" in ""|"关闭"|"shut off") say "已关机 ✓"; return 0 ;; esac
        sleep 3
    done
    say "ACPI 超时，强制 destroy"
    jf_vsh destroy "$VM" >/dev/null 2>&1 || true
    say "已停止（磁盘保留，下次 up 秒起）"
    return 0
}

cmd_status() {
    echo "── Windows 靶机状态 ──"
    echo "域        : $VM"
    echo "通道      : ${JF_LIBVIRT_URI:-?} (${JF_WIN_MODE:-?})"
    echo "磁盘      : ${JF_WIN_DISK:-$JF_SESS_DISK} ($(du -h "${JF_WIN_DISK:-$JF_SESS_DISK}" 2>/dev/null | cut -f1 || echo 不存在))"
    local st; st="$(dom_state)"; echo "状态      : ${st:-未定义}"
    local disp; disp="$(jf_vsh domdisplay "$VM" 2>/dev/null || true)"; [ -n "$disp" ] && echo "VNC       : $disp"
    if jf_vsh qemu-agent-command "$VM" '{"execute":"guest-ping"}' >/dev/null 2>&1; then
        echo "QGA       : 就绪 ✓"
        local ip
        ip="$(jf_vsh domifaddr "$VM" --source agent 2>/dev/null | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' | grep -v '^127\.' | head -1 || true)"
        [ -n "$ip" ] && echo "IP        : $ip"
    else
        echo "QGA       : 未就绪"
    fi
    return 0
}

cmd_shot() {
    local out="${1:-$OUT_DIR/shot.png}"
    mkdir -p "$(dirname "$out")"
    jf_vsh screenshot "$VM" "$out" >/dev/null 2>&1 || die "截屏失败"
    say "已保存 $out"
}

cmd_net() {
    [ "$JF_WIN_MODE" = "system" ] || { say "session 模式使用 user 网络，不支持 virsh net-* 断网注入（可用客机侧防火墙模拟）"; return 0; }
    case "${1:-}" in
        down) jf_vsh net-destroy default >/dev/null 2>&1 || true; say "已断网" ;;
        up)   jf_vsh net-start default >/dev/null 2>&1 || true; say "已恢复" ;;
        *) die "用法：$0 net up|down" ;;
    esac
}

cmd_snap() {
    local name="${1:-$SNAP}"
    jf_vsh snapshot-create-as "$VM" "$name" --disk-only --atomic >/dev/null 2>&1 \
        || jf_vsh snapshot-create-as "$VM" "$name" >/dev/null 2>&1 \
        || die "建快照失败"
    say "快照 $name 已创建"
}

cmd_revert() {
    local name="${1:-$SNAP}"
    case "$(dom_state)" in 运行|running) jf_vsh destroy "$VM" >/dev/null 2>&1 || true ;; esac
    jf_vsh snapshot-revert "$VM" "$name" >/dev/null 2>&1 || die "回滚失败（快照 $name 是否存在？）"
    say "已回滚到 $name"
}

cmd_footprint() {
    local before after
    before="$(free -m | awk '/^Mem:/{print $3}')"
    cmd_down >/dev/null 2>&1 || true
    sleep 2
    after="$(free -m | awk '/^Mem:/{print $3}')"
    echo "停止前已用内存: ${before} MB"
    echo "停止后已用内存: ${after} MB"
    echo "释放: $((before - after)) MB"
    if pgrep -f "guest=${VM}" >/dev/null 2>&1; then
        say "警告：仍有 qemu 进程残留"
        return 1
    fi
    say "无 qemu 进程残留 ✓"
    return 0
}

# 除 probe/uri 外，所有命令先解析通道；解析失败但域可能在另一条通道时，
# 退回“两条都试一遍”的只读查询，避免把“通道没解析”显示成“域不存在”。
ensure_uri_or_die() {
    jf_select_uri && return 0
    die "两条 libvirt 通道都不可用（system 守护进程未起，session 连不上）"
}

case "${1:-probe}" in
    probe)     cmd_probe ;;
    uri)       jf_select_uri && echo "$JF_LIBVIRT_URI" ;;
    up)        ensure_uri_or_die; cmd_up ;;
    down)      ensure_uri_or_die; cmd_down ;;
    status)    ensure_uri_or_die; cmd_status ;;
    shot)      ensure_uri_or_die; shift; cmd_shot "$@" ;;
    net)       ensure_uri_or_die; shift; cmd_net "$@" ;;
    snap)      ensure_uri_or_die; shift; cmd_snap "$@" ;;
    revert)    ensure_uri_or_die; shift; cmd_revert "$@" ;;
    footprint) ensure_uri_or_die; cmd_footprint ;;
    *) echo "用法：$0 {probe|up|down|status|shot|net|snap|revert|footprint|uri}" >&2; exit 2 ;;
esac
