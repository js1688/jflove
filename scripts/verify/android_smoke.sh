#!/usr/bin/env bash
# Android 设备级冒烟：窗口化模拟器 + 安装 + 启动 + 截图。
#
# 用法：
#   scripts/verify/android_smoke.sh              # 默认 AVD jflove-api36，保留模拟器
#   scripts/verify/android_smoke.sh --stop       # 结束后关闭模拟器
#   APK=/path/app-debug.apk AVD=jflove-api36 scripts/verify/android_smoke.sh
#
# 判据：adb devices 出现 emulator-* device；安装 Success；截图文件非空。
# 注意：模拟器必须窗口化 + -gpu host；-no-window 会 framebuffer 失败，
#       swiftshader/guest 会 SIGSEGV（见 lessons L49）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/scripts/verify/env.sh"

APK="${APK:-$ROOT/jflove-app/build/app/outputs/flutter-apk/app-debug.apk}"
AVD="${AVD:-jflove-api36}"
OUT="${OUT:-/tmp/jf-android.png}"
STOP=0
[ "${1:-}" = "--stop" ] && STOP=1

[ -f "$APK" ] || jf_die "APK 不存在：$APK（先 flutter build apk --debug）"
export FLUTTER_HOME="$JF_FLUTTER_HOME"
export ANDROID_HOME="$JF_ANDROID_HOME"
export PATH="$FLUTTER_HOME/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"
command -v adb >/dev/null || jf_die "找不到 adb（ANDROID_HOME 是否正确？）"

# 模拟器需要真实 X 显示；优先复用/启动独立 Xvnc
DISP="${JF_ANDROID_DISPLAY:-:99}"
DISP="$(jf_start_xvnc "$DISP")"
jf_start_wm "$DISP"

if ! adb devices | grep -qE "emulator-[0-9]+[[:space:]]+device"; then
    echo "[verify] 启动模拟器 $AVD（DISPLAY=$DISP，-gpu host）"
    DISPLAY="$DISP" nohup emulator -avd "$AVD" -gpu host -no-audio -no-boot-anim \
        -no-snapshot -read-only >/tmp/jf-emulator.log 2>&1 &
    echo $! >/tmp/jf-emulator.pid
fi

timeout 180 adb wait-for-device || jf_die "等待模拟器超时，见 /tmp/jf-emulator.log"
for _ in $(seq 1 120); do
    [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ] && break
    sleep 1
done
[ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ] || jf_die "模拟器未完成启动"

# 《验证标准》§4：只允许当前版本 APK
AAPT_BIN="$(ls "${ANDROID_HOME:-$HOME/sdk/android-sdk}"/build-tools/*/aapt 2>/dev/null | head -1)"
VER_JSON="$(python3 -c 'import json;print(json.load(open("version.json"))["version"])')"
APK_VER="$("$AAPT_BIN" dump badging "$APK" 2>/dev/null | sed -n "s/.*versionName='\([^']*\)'.*/\1/p" | head -1)"
if [ -n "$APK_VER" ] && [ "$APK_VER" != "$VER_JSON" ]; then
    echo "[verify] APK 版本 $APK_VER != 当前版本 $VER_JSON，请先构建当前版本（拒绝旧包）" >&2
    exit 1
fi
jf_pass "安装 $APK（versionName=$APK_VER，当前=$VER_JSON）"
adb install -r "$APK" | tail -1
adb shell am start -n com.jflove.jflove_app/.MainActivity >/dev/null
sleep 8

adb shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1 || true
adb pull /sdcard/ui.xml /tmp/jf-android-ui.xml >/dev/null 2>&1 || true
adb exec-out screencap -p >"$OUT"

jf_pass "截图：$OUT（UI 树：/tmp/jf-android-ui.xml）"
# 《验证标准》§5：必须有一次输入产生状态变化（不能只截图）
BEFORE="$(sha256sum "$OUT" | cut -d' ' -f1)"
adb shell input keyevent 4 >/dev/null 2>&1 || true
sleep 1
AFTER_SHOT="${OUT%.png}-after.png"
adb exec-out screencap -p >"$AFTER_SHOT"
AFTER="$(sha256sum "$AFTER_SHOT" | cut -d' ' -f1)"
[ "$BEFORE" != "$AFTER" ] || jf_die "输入后界面无变化（交互未生效）"
jf_pass "交互：keyevent 生效（截图变化，证据 $AFTER_SHOT）"

if [ "$STOP" = "1" ]; then
    adb emu kill >/dev/null 2>&1 || true
    jf_pass "已关闭模拟器"
fi
