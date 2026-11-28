#!/usr/bin/env bash
# Android 模拟器「全自动」入口 —— 自检 → 自启 → 自停，无需人工
#
# 为什么存在：probe.sh 曾把「AVD 存在但模拟器未启动」报成 no-device，
# 导致整端被跳过。实测：AVD jflove-api36 在 Xvnc + -gpu host 下 20 秒内可启动。
#
# 用法：
#   scripts/verify/android_emulator.sh probe    # 自检：SDK/AVD/加速能力
#   scripts/verify/android_emulator.sh start    # 启动（自动配 Xvnc，已启动则复用）
#   scripts/verify/android_emulator.sh stop     # 停止
#   scripts/verify/android_emulator.sh status   # 状态 / 设备号 / boot 完成标记
#   scripts/verify/android_emulator.sh install <apk>   # 装当前版本 APK（覆盖旧包）
#
# 退出码：0 成功/具备；3 环境不具备；4 无 AVD；5 启动失败
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/scripts/verify/env.sh" >/dev/null 2>&1 || true

export ANDROID_HOME="${ANDROID_HOME:-$HOME/sdk/android-sdk}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
ADB="$ANDROID_HOME/platform-tools/adb"
EMU="$ANDROID_HOME/emulator/emulator"
AVD="${JF_AVD:-jflove-api36}"
PIDFILE="/tmp/jf-emulator.pid"
LOG="/tmp/jf-emulator.log"

say() { printf '[\033[36mandroid\033[0m] %s\n' "$*"; }
err() { printf '[\033[31mandroid\033[0m] %s\n' "$*" >&2; }

devices() { "$ADB" devices 2>/dev/null | awk '/device$/{print $1}'; }
booted() { [ "$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; }

cmd_probe() {
    local ok=0
    [ -x "$ADB" ] && say "adb ✓ ($ADB)" || { err "缺 adb：$ADB"; ok=1; }
    [ -x "$EMU" ] && say "emulator ✓" || { err "缺 emulator：$EMU"; ok=1; }
    if [ -x "$EMU" ]; then
        local avds; avds="$("$EMU" -list-avds 2>/dev/null | tr '\n' ' ')"
        if printf '%s' "$avds" | grep -qw "$AVD"; then
            say "AVD $AVD 存在 ✓（共：$avds）"
        else
            err "AVD $AVD 不存在（现有：${avds:-无}）"; ok=1
        fi
    fi
    # KVM 加速（x86_64 镜像必须有，否则慢到不可用）
    if [ -r /dev/kvm ] && [ -w /dev/kvm ]; then say "/dev/kvm ✓（硬件加速可用）"
    else say "/dev/kvm 不可用 → 只能软件模拟，会极慢"; fi
    [ "$ok" -eq 0 ] && { say "结论：具备运行条件"; return 0; }
    return 3
}

cmd_start() {
    cmd_probe >/dev/null 2>&1 || { err "环境不具备"; return 3; }

    if [ -n "$(devices)" ] && booted; then
        say "已有设备在运行：$(devices | tr '\n' ' ')"; return 0
    fi

    # 模拟器需要真实显示；无 X 时自建 Xvnc（L50：-no-window 会慢且不稳定）
    local disp="${DISPLAY:-}"
    if [ -z "$disp" ] || ! DISPLAY="$disp" xdpyinfo >/dev/null 2>&1; then
        disp="$(jf_start_xvnc :99 2>/dev/null || echo ':99')"
        say "使用 Xvnc 显示：$disp"
    fi

    say "启动 $AVD（-gpu host，加速渲染）…"
    nohup env DISPLAY="$disp" ANDROID_HOME="$ANDROID_HOME" ANDROID_SDK_ROOT="$ANDROID_SDK_ROOT" \
        "$EMU" -avd "$AVD" -gpu host -no-snapshot-save -no-audio -no-boot-anim \
        >"$LOG" 2>&1 &
    echo $! >"$PIDFILE"

    say "等待设备上线（最多 180s）…"
    local i
    for ((i = 0; i < 60; i++)); do
        sleep 3
        if [ -n "$(devices)" ] && booted; then
            say "已就绪 ✓ 设备：$(devices | tr '\n' ' ')"
            cmd_status
            return 0
        fi
    done
    err "启动超时，见 $LOG"; return 5
}

cmd_stop() {
    if [ -n "$(devices)" ]; then
        say "adb 关机…"
        "$ADB" emu kill >/dev/null 2>&1 || true
    fi
    if [ -f "$PIDFILE" ]; then
        kill "$(cat "$PIDFILE")" 2>/dev/null || true
        rm -f "$PIDFILE"
    fi
    for _ in $(seq 1 20); do
        [ -z "$(devices)" ] && break
        sleep 1
    done
    say "已停止"
    return 0
}

cmd_status() {
    echo "── Android 模拟器状态 ──"
    echo "AVD      : $AVD"
    echo "SDK      : $ANDROID_HOME"
    local d; d="$(devices | tr '\n' ' ')"
    echo "设备     : ${d:-无}"
    if [ -n "$d" ]; then
        echo "boot     : $("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')"
        echo "版本     : $("$ADB" shell getprop ro.build.version.release 2>/dev/null | tr -d '\r') (API $("$ADB" shell getprop ro.build.version.sdk 2>/dev/null | tr -d '\r'))"
        echo "已装应用 : $("$ADB" shell pm list packages 2>/dev/null | grep -i jflove | head -3 | tr '\n' ' ')"
    fi
    return 0
}

cmd_install() {
    local apk="$1"
    [ -r "$apk" ] || { err "APK 不可读：$apk"; return 4; }
    [ -n "$(devices)" ] || { err "无设备，先跑 $0 start"; return 3; }
    say "安装 $(basename "$apk")（覆盖旧版本）"
    "$ADB" install -r -d "$apk" 2>&1 | tail -3
    return 0
}

case "${1:-status}" in
    probe)   cmd_probe ;;
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    status)  cmd_status ;;
    install) shift; cmd_install "$@" ;;
    *) echo "用法：$0 {probe|start|stop|status|install <apk>}" >&2; exit 2 ;;
esac
