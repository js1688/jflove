#!/usr/bin/env bash
# 验证入口公共环境（source 使用，不直接执行）
#
# 目的：把「锁屏会话 / 无显示器 / SDK 路径 / wine 路径」这些机器差异集中到这里，
# 其它 verify 脚本只依赖本文件导出的变量与函数。

JF_ROOT="${JF_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
JF_PY="${JF_PY:-$JF_ROOT/jflove-server/venv-linux/bin/python}"
JF_DESKTOP_PY="${JF_DESKTOP_PY:-$JF_ROOT/jflove-desktop/venv-linux/bin/python}"
JF_SERVER_PORT="${JF_SERVER_PORT:-8989}"
JF_WEB_PORT="${JF_WEB_PORT:-3000}"

# 独立 GUI 显示：默认 :99；RFB 端口 = 5900 + N，避免和用户会话冲突
JF_XVNC_DISPLAY="${JF_XVNC_DISPLAY:-:99}"
JF_XVNC_GEOMETRY="${JF_XVNC_GEOMETRY:-1440x900}"

# Windows/wine：系统 /usr/bin/wine 可能是断链，默认用 PortProton 自带运行时
JF_WINE_BIN="${JF_WINE_BIN:-$HOME/PortProton/data/dist/WINE_LG_11-1/bin/wine}"
JF_WINEPREFIX="${JF_WINEPREFIX:-$HOME/tmp/jflove-wine}"

# Flutter / Android（AGENTS §3.1：SDK 物理路径必须是 ASCII）
JF_FLUTTER_HOME="${FLUTTER_HOME:-$HOME/sdk/flutter}"
JF_ANDROID_HOME="${ANDROID_HOME:-$HOME/sdk/android-sdk}"

# 无桌面登录时 /run/user/$UID 可能不存在（没有 pam_systemd 用户会话）；
# kwin_x11 / dbus 会话需要运行时目录，这里给一个 0700 的回退。
if [ -z "${XDG_RUNTIME_DIR:-}" ] || [ ! -d "${XDG_RUNTIME_DIR}" ]; then
    if [ -d "/run/user/$(id -u)" ]; then
        export XDG_RUNTIME_DIR="/run/user/$(id -u)"
    else
        export XDG_RUNTIME_DIR="/tmp/jf-runtime-$(id -u)"
        mkdir -p "$XDG_RUNTIME_DIR"
        chmod 700 "$XDG_RUNTIME_DIR"
    fi
fi

jf_have() { command -v "$1" >/dev/null 2>&1; }

jf_die() {
    echo "[verify] $*" >&2
    exit 1
}

jf_pass() {
    echo "[verify] $*"
}

# 启动（或复用）独立 Xvnc，输出可用的 DISPLAY
jf_start_xvnc() {
    local disp="${1:-$JF_XVNC_DISPLAY}"
    local n="${disp#:}"
    local rfb=$((5900 + n))
    if DISPLAY="$disp" xdpyinfo >/dev/null 2>&1; then
        echo "$disp"
        return 0
    fi
    jf_have Xvnc || jf_die "缺少 Xvnc（Fedora: tigervnc-server）"
    Xvnc "$disp" -geometry "$JF_XVNC_GEOMETRY" -depth 24 \
        -SecurityTypes None -AlwaysShared -rfbport "$rfb" -nolisten tcp \
        >"/tmp/jf-xvnc-${n}.log" 2>&1 &
    echo $! >"/tmp/jf-xvnc-${n}.pid"
    for _ in $(seq 1 50); do
        DISPLAY="$disp" xdpyinfo >/dev/null 2>&1 && { echo "$disp"; return 0; }
        sleep 0.2
    done
    jf_die "Xvnc $disp 启动失败，见 /tmp/jf-xvnc-${n}.log"
}

# Xvnc 没有 WM 时 Qt 的键盘事件不生效（窗口不 active），必须配一个 WM
jf_start_wm() {
    local disp="$1"
    local n="${disp#:}"
    if DISPLAY="$disp" xprop -root _NET_SUPPORTING_WM_CHECK 2>/dev/null | grep -q "window id"; then
        return 0
    fi
    jf_have kwin_x11 || jf_die "缺少 kwin_x11（KDE 环境自带）"
    DISPLAY="$disp" dbus-run-session -- kwin_x11 --replace \
        >"/tmp/jf-kwin-${n}.log" 2>&1 &
    echo $! >"/tmp/jf-kwin-${n}.pid"
    sleep 2
}

# 抓一帧；Xvnc 上 ffmpeg x11grab 比 ImageMagick import 可靠
jf_capture() {
    local disp="$1" out="$2"
    jf_have ffmpeg || jf_die "缺少 ffmpeg"
    DISPLAY="$disp" ffmpeg -hide_banner -loglevel error \
        -f x11grab -video_size "$JF_XVNC_GEOMETRY" -i "$disp" \
        -frames:v 1 -y "$out"
    echo "$out"
}

# 等待 HTTP 可用
jf_wait_http() {
    local url="$1" tries="${2:-100}"
    for _ in $(seq 1 "$tries"); do
        curl -fsS --max-time 2 "$url" >/dev/null 2>&1 && return 0
        sleep 0.2
    done
    return 1
}
