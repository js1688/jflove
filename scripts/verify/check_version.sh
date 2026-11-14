#!/usr/bin/env bash
# 当前版本一致性校验（《验证标准》§4）
#
# 只读：version.json / sync_version / 已构建产物 / 运行中的 VM / 运行中的模拟器。
# 输出 JSON；任一项“明确不一致”退出 1；“环境不可用”记 skip，不判失败。
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
: "${ANDROID_HOME:=$HOME/sdk/android-sdk}"
VER="$(python3 -c 'import json;print(json.load(open("version.json"))["version"])')"
RESULT="{\"version\":\"$VER\""

sync_ok=1
python3 scripts/sync_version.py --check >/dev/null 2>&1 || sync_ok=0
RESULT="$RESULT,\"sync_version\":\"$([ $sync_ok -eq 1 ] && echo ok || echo mismatch)\""

# Android APK（若存在当前构建）
APK="jflove-app/build/app/outputs/flutter-apk/app-debug.apk"
apk_ver="skip"
if [ -f "$APK" ]; then
    AAPT="$("$ANDROID_HOME"/build-tools/*/aapt2 2>/dev/null | head -1)"
    AAPT="$(ls "$ANDROID_HOME"/build-tools/*/aapt 2>/dev/null | head -1)"
    if [ -n "$AAPT" ] && [ -x "$AAPT" ]; then
        apk_ver="$("$AAPT" dump badging "$APK" 2>/dev/null | sed -n "s/.*versionName='\([^']*\)'.*/\1/p" | head -1)"
    fi
fi
RESULT="$RESULT,\"android_apk\":\"${apk_ver:-skip}\""

# Windows VM 注册表（若 VM 在跑）
vm="$(virsh -c qemu:///system domstate "${JF_WIN_VM:-jflove-win10}" 2>/dev/null | head -1 | tr -d '[:space:]')"
win_ver="skip"
if [ "$vm" = "运行" ] || [ "$vm" = "running" ]; then
    win_ver="$(python3 scripts/verify/windows_vm.py exec --ps "Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' | Where-Object { \\\$_.DisplayName -like '*JFLove*' } | Select-Object -First 1 -ExpandProperty DisplayVersion" 2>/dev/null | tr -d '\r' | tail -1)"
fi
RESULT="$RESULT,\"windows_installed\":\"${win_ver:-skip}\""

# Linux 源码
desk_ver="$(python3 -c "import re,sys; s=open('jflove-desktop/src/config/settings.py',encoding='utf-8').read(); m=re.search(r'APP_VERSION\\s*=\\s*\"([^\"]+)\"',s); print(m.group(1) if m else '')" 2>/dev/null)"
RESULT="$RESULT,\"desktop_source\":\"${desk_ver:-skip}\""
RESULT="$RESULT}"

echo "$RESULT" | python3 -c '
import json,sys
d=json.load(sys.stdin)
ver=d["version"]
bad=[k for k in ("sync_version","android_apk","windows_installed","desktop_source") if d.get(k) not in ("ok","skip",ver)]
print(json.dumps(d,ensure_ascii=False))
sys.exit(1 if bad else 0)
'
