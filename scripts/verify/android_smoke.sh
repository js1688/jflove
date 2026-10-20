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

jf_pass "安装 $APK"
adb install -r "$APK" | tail -1
adb shell am start -n com.jflove.jflove_app/.MainActivity >/dev/null
sleep 8

adb shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1 || true
adb pull /sdcard/ui.xml /tmp/jf-android-ui.xml >/dev/null 2>&1 || true
adb exec-out screencap -p >"$OUT"

jf_pass "截图：$OUT（UI 树：/tmp/jf-android-ui.xml）"
jf_pass "操作：adb shell input tap/text（xinput.py 仅用于 X11 桌面）"

if [ "$STOP" = "1" ]; then
    adb emu kill >/dev/null 2>&1 || true
    jf_pass "已关闭模拟器"
fi
