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

adb_bin="${ANDROID_HOME:-$HOME/sdk/android-sdk}/platform-tools/adb"
android="missing"
if [ -x "$adb_bin" ]; then
    if "$adb_bin" devices 2>/dev/null | grep -q 'device$'; then android="device"; else android="no-device"; fi
fi

python3 - "$vm_state" "$linux_display" "$linux_wayland" "$android" <<'PY'
import json, sys
print(json.dumps({
    "windows_vm": sys.argv[1],
    "linux_display": sys.argv[2],
    "linux_wayland": sys.argv[3],
    "android": sys.argv[4],
}, ensure_ascii=False))
PY
