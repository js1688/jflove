#!/usr/bin/env bash
# 一键环境编排：把验证所需的「显示 / Android 模拟器 / Windows 靶机」全部拉到可用状态。
#
# 存在意义：
#   本机 Windows 靶机与 Android AVD 是常备资产，只会「没启动」不会「没有」。
#   过去每次验证都要人工判断「能不能跑」，误判后甚至重装了 Windows。此脚本把
#   判断 + 自启 + 复验收敛成一条命令，供开发和测试脚本直接调用。
#
# 用法：
#   scripts/verify/env_up.sh up       # 拉起全部（幂等：已在跑就复用）
#   scripts/verify/env_up.sh down     # 停掉全部（释放内存/CPU）
#   scripts/verify/env_up.sh status   # 只读：当前各端可用性（不启动任何东西）
#   scripts/verify/env_up.sh up --only windows,android   # 只拉指定端
#
# 退出码：
#   0 = 全部就绪
#   3 = 环境能力缺失（无 /dev/kvm、无 AVD、无磁盘）—— 这才是"真不具备"
#   5 = 尝试拉起但失败
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/scripts/verify/env.sh" >/dev/null 2>&1 || true

ONLY="all"
say()  { printf '[\033[36menv-up\033[0m] %s\n' "$*"; }
warn() { printf '[\033[33menv-up\033[0m] %s\n' "$*" >&2; }
err()  { printf '[\033[31menv-up\033[0m] %s\n' "$*" >&2; }

want() {
    [ "$ONLY" = "all" ] && return 0
    case ",$ONLY," in *",$1,"*) return 0 ;; *) return 1 ;; esac
}

# 读取 probe 的 JSON 字段
probe_field() {
    bash "$ROOT/scripts/verify/probe.sh" 2>/dev/null \
        | python3 -c "import json,sys;print(json.load(sys.stdin).get('$1',''))" 2>/dev/null
}

cmd_status() {
    local ready chan android linux_ui
    ready="$(probe_field windows_vm_ready)"
    chan="$(probe_field windows_vm_channel)"
    android="$(probe_field android)"
    linux_ui="$(probe_field linux_ui)"

    echo "── 验证环境可用性 ──"
    printf '%-10s %-14s %s\n' "端" "状态" "说明"
    printf '%-10s %-14s %s\n' "显示" "$linux_ui" \
        "$( [ "$linux_ui" = "none" ] && echo '无显示（可 offscreen+CDP）' || echo '可用' )"
    printf '%-10s %-14s %s\n' "Windows" "${ready:-unknown}" \
        "$( case "$ready" in
              yes) echo "已运行（$chan）" ;;
              starting) echo "开机中，QGA 未就绪" ;;
              recoverable) echo "可自动拉起（磁盘在）" ;;
              *) echo "无可用靶机" ;;
            esac )"
    printf '%-10s %-14s %s\n' "Android" "${android:-unknown}" \
        "$( case "$android" in
              device) echo '已连接' ;;
              avd-not-started) echo 'AVD 在，未启动（可自动起）' ;;
              *) echo '无可用 AVD' ;;
            esac )"

    [ "$ready" = "yes" ] || [ "$ready" = "starting" ] || [ "$ready" = "recoverable" ] || return 3
    [ "$android" = "device" ] || [ "$android" = "avd-not-started" ] || return 3
    return 0
}

cmd_up() {
    local rc=0
    # 1) 显示：桌面端验证需要（无 X 时用 Xvnc）
    if want display; then
        local disp
        disp="$(jf_start_xvnc :99 2>/dev/null || echo '')"
        if [ -n "$disp" ]; then say "显示就绪：$disp"; else warn "Xvnc 启动失败（不影响 Windows/Android 本身）"; fi
    fi

    # 2) Windows 靶机
    if want windows; then
        local ready; ready="$(probe_field windows_vm_ready)"
        case "$ready" in
            yes) say "Windows 已就绪（跳过启动）" ;;
            starting) say "Windows 开机中，等待 QGA…"; timeout 300 bash "$ROOT/scripts/verify/vm-win.sh" up >/dev/null 2>&1 && say "Windows 就绪 ✓" || { err "Windows 等待超时"; rc=5; } ;;
            recoverable)
                say "Windows 关机/未定义，自动拉起…"
                if timeout 420 bash "$ROOT/scripts/verify/vm-win.sh" up >/dev/null 2>&1; then
                    say "Windows 就绪 ✓"
                else
                    err "Windows 拉起失败（看 outputs：vm-win.sh status）"; rc=5
                fi
                ;;
            *)
                # 尝试一次；若磁盘都没有，才判真缺失
                if timeout 420 bash "$ROOT/scripts/verify/vm-win.sh" up >/dev/null 2>&1; then
                    say "Windows 就绪 ✓"
                else
                    err "Windows 靶机不可用（无域且无磁盘）"; rc=3
                fi
                ;;
        esac
    fi

    # 3) Android
    if want android; then
        local a; a="$(probe_field android)"
        case "$a" in
            device) say "Android 已就绪（跳过启动）" ;;
            avd-not-started)
                say "Android AVD 未启动，自动拉起…"
                if timeout 300 bash "$ROOT/scripts/verify/android_emulator.sh" start >/dev/null 2>&1; then
                    say "Android 就绪 ✓"
                else
                    err "Android 启动失败"; rc=5
                fi
                ;;
            *)
                err "Android 无可用 AVD（这才需要重建）"; rc=3
                ;;
        esac
    fi

    echo
    cmd_status
    local st=$?
    [ "$rc" -ne 0 ] && return "$rc"
    return "$st"
}

cmd_down() {
    if want windows; then
        bash "$ROOT/scripts/verify/vm-win.sh" down >/dev/null 2>&1 && say "Windows 已停止" || warn "Windows 停止失败或本就未运行"
    fi
    if want android; then
        bash "$ROOT/scripts/verify/android_emulator.sh" stop >/dev/null 2>&1 && say "Android 已停止" || warn "Android 停止失败或本就未运行"
    fi
    say "（Xvnc 保留，供桌面端复用；如需释放：kill \$(cat /tmp/jf-xvnc-99.pid)）"
    return 0
}

case "${1:-status}" in
    up)
        shift || true
        while [ $# -gt 0 ]; do
            case "$1" in
                --only) shift; ONLY="${1:-all}" ;;
                *) err "未知参数：$1"; exit 2 ;;
            esac
            shift
        done
        cmd_up
        ;;
    down)   shift || true
            while [ $# -gt 0 ]; do
                case "$1" in --only) shift; ONLY="${1:-all}" ;; *) err "未知参数：$1"; exit 2 ;; esac
                shift
            done
            cmd_down ;;
    status) cmd_status ;;
    *) echo "用法：$0 {up|down|status} [--only windows,android,display]" >&2; exit 2 ;;
esac
