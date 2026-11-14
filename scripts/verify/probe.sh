#!/usr/bin/env bash
# 验证入口统一探测（先跑它，再判“能否跳过”）
#
# 教训（2026-11-14）：用 `virsh list`（默认 qemu:///session）看不到 qemu:///system 的域，
# 就误报“没有 Windows VM”；Linux 只试 :99 就断言“无显示”，没看 loginctl / wayland socket。
# 结论：禁止用默认命令/默认 URI 直接判缺失。
#
# 输出 JSON（退出码 0；缺件写在字段里）：
#   {"windows_vm":"running|shut off|missing","linux_display":"x11::0|missing",
#    "linux_wayland":"wayland-1|","android":"device|no-device|missing"}
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VM="${JF_WIN_VM:-jflove-win10}"
UID_NUM="$(id -u)"

vm_state="$(virsh -c qemu:///system domstate "$VM" 2>/dev/null | head -1 | tr -d '[:space:]' || true)"
[ -z "$vm_state" ] && vm_state="missing"

linux_display="missing"
for d in :0 :1; do
    if DISPLAY="$d" xdpyinfo >/dev/null 2>&1; then linux_display="x11:${d}"; break; fi
done

linux_wayland=""
if ls /run/user/"$UID_NUM"/wayland-* >/dev/null 2>&1; then
    linux_wayland="$(basename "$(ls /run/user/"$UID_NUM"/wayland-* 2>/dev/null | grep -v '\.lock$' | head -1)")"
fi

# Linux 无 X 也能验证：offscreen 平台 + QtWebEngine CDP（已验证 9222 可 attach）
linux_ui="none"
if [ "$linux_display" != "missing" ]; then
    linux_ui="x11"
elif [ -x "$ROOT/jflove-desktop/venv-linux/bin/python" ] && [ -f "$ROOT/scripts/verify/desktop_shot.py" ]; then
    linux_ui="offscreen+cdp"
fi
xvnc="missing"; command -v Xvnc >/dev/null 2>&1 && xvnc="installed"
ydotool="missing"; command -v ydotool >/dev/null 2>&1 && ydotool="installed"
xinput="missing"; command -v xinput >/dev/null 2>&1 && xinput="installed"
android_avd="none"
emu="${ANDROID_HOME:-$HOME/sdk/android-sdk}/emulator/emulator"
if [ -x "$emu" ]; then android_avd="$("$emu" -list-avds 2>/dev/null | head -1)"; [ -z "$android_avd" ] && android_avd="none"; fi
offscreen_cdp="no"
if [ -x "$ROOT/jflove-desktop/venv-linux/bin/python" ] && [ -f "$ROOT/scripts/verify/desktop_shot.py" ]; then offscreen_cdp="yes"; fi

adb_bin="${ANDROID_HOME:-$HOME/sdk/android-sdk}/platform-tools/adb"
android="missing"
if [ -x "$adb_bin" ]; then
    if "$adb_bin" devices 2>/dev/null | grep -q 'device$'; then android="device"; else android="no-device"; fi
fi

python3 - "$vm_state" "$linux_display" "$linux_wayland" "$linux_ui" "$xvnc" "$ydotool" "$xinput" "$android" "$android_avd" "$offscreen_cdp" <<'PY'
import json, sys
print(json.dumps({
    "windows_vm": sys.argv[1],
    "linux_display": sys.argv[2],
    "linux_wayland": sys.argv[3],
    "linux_ui": sys.argv[4],
    "xvnc": sys.argv[5],
    "ydotool": sys.argv[6],
    "xinput": sys.argv[7],
    "android": sys.argv[8],
    "android_avd": sys.argv[9],
    "offscreen_cdp": sys.argv[10],
}, ensure_ascii=False))
PY
