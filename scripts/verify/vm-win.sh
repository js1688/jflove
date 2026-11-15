#!/usr/bin/env bash
# Windows 10 真机验证入口（KVM/libvirt 虚拟机）——Windows 端一律走本脚本。
#
# 为什么要有它：
#   模拟层覆盖不到 Inno 安装器、文件关联、Defender/SmartScreen、媒体解码与真实 Win32 行为；
#   Phase 6.5 人工体验撤销后，Windows 端的证据必须由这台 VM 自动产出。
#
# 用法：
#   scripts/verify/vm-win.sh up                 # 起网络 + 起 VM + 等 QGA
#   scripts/verify/vm-win.sh down               # ACPI 关机 → 兜底 destroy → 断言零占用
#   scripts/verify/vm-win.sh status             # 状态 / IP / QGA / 宿主机资源
#   scripts/verify/vm-win.sh shot [out]         # 截屏（默认 /tmp/jf-win/shot.png）
#   scripts/verify/vm-win.sh net up|down        # 断网注入（验证断点续传/重连）
#   scripts/verify/vm-win.sh snap [name]        # 建磁盘快照（默认 clean）
#   scripts/verify/vm-win.sh revert [name]      # 回滚到快照（先关机）
#   scripts/verify/vm-win.sh footprint          # 实测「停掉后不占 CPU/内存」并打印对比
#
# 客机内执行 / 传文件走 python3 scripts/verify/windows_vm.py（QGA 通道）。
#
# 前提：本机已装 qemu-kvm-core + libvirt + virt-install，且存在域 $JF_WIN_VM。
# 首次创建 VM 的步骤见 scripts/verify/README.md「Windows 真机（KVM）」章节。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VM="${JF_WIN_VM:-jflove-win10}"
SNAP="${JF_WIN_SNAP:-clean}"
OUT_DIR="${JF_WIN_OUT:-/tmp/jf-win}"
PY="$ROOT/scripts/verify/windows_vm.py"

mkdir -p "$OUT_DIR"

vsh() { virsh -c qemu:///system "$@"; }
say() { printf '[vm-win] %s\n' "$*"; }
die() { printf '[vm-win] 失败：%s\n' "$*" >&2; exit 1; }

#: 该 VM 的 qemu 进程命令行特征（用于断言"停掉就没了"）
proc_pattern() { echo "jflove-win10"; }

dom_state() { vsh domstate "$VM" 2>/dev/null | head -1 || true; }

wait_agent() {
    local timeout="${1:-180}" i
    for ((i = 0; i < timeout / 2; i++)); do
        if python3 "$PY" exec "ver" >/dev/null 2>&1; then return 0; fi
        sleep 2
    done
    return 1
}

ensure_net() {
    if ! vsh net-info default 2>/dev/null | grep -q '活动： *是\|Active: *yes'; then
        vsh net-start default >/dev/null 2>&1 || true
    fi
}

cmd_up() {
    ensure_net
    case "$(dom_state)" in
        运行|running) say "已在运行" ;;
        *) say "启动 $VM"; vsh start "$VM" ;;
    esac
    if wait_agent 240; then
        say "QGA 就绪，客机 IP=$(python3 "$PY" ip 2>/dev/null || echo '?')"
    else
        die "QGA 未就绪（看截图：scripts/verify/vm-win.sh shot）"
    fi
}

cmd_down() {
    local before after
    before="$(free -m | awk '/^Mem:/{print $3}')"
    if [ "$(dom_state)" != "运行" ]; then
        say "本来就已关机"
    else
        say "ACPI 关机"
        vsh shutdown "$VM" >/dev/null 2>&1 || true
        for _ in $(seq 1 60); do
            [ "$(dom_state)" = "运行" ] || break
            sleep 2
        done
        if [ "$(dom_state)" = "运行" ]; then
            say "ACPI 超时，强制 destroy"
            vsh destroy "$VM" >/dev/null 2>&1 || true
            sleep 2
        fi
    fi
    # 零占用断言：进程必须消失
    if pgrep -af "qemu-system.*$(proc_pattern)" >/dev/null 2>&1; then
        die "仍有 qemu 进程存活：$(pgrep -af "qemu-system.*$(proc_pattern)" | head -2)"
    fi
    after="$(free -m | awk '/^Mem:/{print $3}')"
    say "已关机｜宿主机已用内存 ${before}M → ${after}M｜qemu 进程 0"
}

cmd_status() {
    printf '域状态: %s\n' "$(dom_state)"
    printf '客机 IP: %s\n' "$(python3 "$PY" ip 2>/dev/null || echo '-')"
    printf 'QGA   : %s\n' "$(python3 "$PY" exec "ver" 2>/dev/null | tr -d '\r\n' || echo '-')"
    free -h | sed -n '1,2p'
    pgrep -af "qemu-system.*$(proc_pattern)" | head -2 || echo "qemu 进程: 无"
}

cmd_shot() {
    local out="${1:-$OUT_DIR/shot.png}" tmp
    tmp="$(mktemp -u /tmp/jf-shot-XXXX.ppm)"
    vsh screenshot "$VM" --file "$tmp" >/dev/null 2>&1 || die "截屏失败（VM 可能已关机）"
    # virsh 实际输出 PNG（内容嗅探后改名，扩展名不影响使用）
    mv "$tmp" "$out"
    say "截图 $out"
}

cmd_net() {
    local action="${1:-}" mac
    case "$action" in up|down) ;; *) die "用法：vm-win.sh net up|down" ;; esac
    mac="$(vsh domiflist "$VM" | awk 'NR==3{print $5}')"
    [ -n "$mac" ] || die "取不到网卡 MAC"
    vsh domif-setlink "$VM" "$mac" "$action" >/dev/null
    say "网卡 $mac → $action"
}

cmd_snap() {
    local name="${1:-$SNAP}"
    vsh snapshot-list "$VM" --name | grep -qx "$name" && { say "快照 $name 已存在"; return 0; }
    vsh snapshot-create-as "$VM" "$name" --description "jflove 自动化基线" >/dev/null
    say "已建快照 $name"
}

cmd_revert() {
    local name="${1:-$SNAP}"
    vsh snapshot-info "$VM" "$name" >/dev/null 2>&1 || die "快照 $name 不存在"
    [ "$(dom_state)" = "运行" ] && { say "先关机再回滚"; cmd_down; }
    vsh snapshot-revert "$VM" "$name" >/dev/null
    say "已回滚到 $name"
}

cmd_footprint() {
    local mem_before mem_idle mem_after cpu_idle
    mem_before="$(free -m | awk '/^Mem:/{print $3}')"
    cmd_up >/dev/null
    sleep 5
    mem_idle="$(free -m | awk '/^Mem:/{print $3}')"
    cpu_idle="$(top -b -n 2 -d 1 | awk '/^%Cpu/{print $2}' | tail -1)"
    cmd_down >/dev/null
    mem_after="$(free -m | awk '/^Mem:/{print $3}')"
    echo "宿主机已用内存：运行中 ${mem_idle}M（较前 ${mem_before}M）→ 关机后 ${mem_after}M"
    echo "运行中空闲 CPU 占用：${cpu_idle}%（Windows 后台已按 setup-guest.ps1 去噪）"
    echo "关机后 qemu 进程：$(pgrep -c -f "qemu-system.*$(proc_pattern)" || true) 个"
}

case "${1:-status}" in
    up)        cmd_up ;;
    down)      cmd_down ;;
    status)    cmd_status ;;
    shot)      shift; cmd_shot "$@" ;;
    net)       shift; cmd_net "$@" ;;
    snap)      shift; cmd_snap "$@" ;;
    revert)    shift; cmd_revert "$@" ;;
    footprint) cmd_footprint ;;
    *) sed -n '1,30p' "$0"; exit 2 ;;
esac
