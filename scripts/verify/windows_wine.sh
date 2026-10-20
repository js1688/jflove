#!/usr/bin/env bash
# Windows 桌面端（wine）验证入口：CI 产物 + ICU 补齐 + 独立显示 + 启动截图。
#
# 用法：
#   scripts/verify/windows_wine.sh                 # 下载/解压/补 ICU/启动/截图
#   scripts/verify/windows_wine.sh --skip-download # 复用已下载的 zip
#   VERSION=v1.7.0 scripts/verify/windows_wine.sh
#
# 前提：
#   - wine：默认 PortProton WINE_LG_11-1（/usr/bin/wine 可能是断链）
#   - WINEPREFIX 必须在 $HOME 下（wine 拒绝 /tmp 这类非本人属主目录）
#   - 首次使用新 prefix 需先装 VC 运行库：winetricks -q vcrun2022
#
# 判据：窗口出现 + 截图非空；进一步操作/登录见 README（xinput.py）。
# 注意：wine 覆盖 UI 可操作，不覆盖 GPU/媒体/Inno 安装器/CfAPI，发布门禁仍以 CI/真机为准。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/scripts/verify/env.sh"

VERSION="${VERSION:-v1.7.0}"
URL="${URL:-https://github.com/js1688/jflove/releases/download/${VERSION}/JFLove-${VERSION#v}-win64-portable.zip}"
ICU_URL="${ICU_URL:-https://github.com/unicode-org/icu/releases/download/release-78.3/icu4c-78.3-Win64-MSVC2022.zip}"
WORK="${WORK:-/tmp/jf-win-verify}"
APP="$WORK/app/JFLove"
ZIP="$WORK/$(basename "$URL")"
DISP="${JF_WIN_DISPLAY:-:98}"
OUT="${OUT:-/tmp/jf-wine.png}"

SKIP=0
[ "${1:-}" = "--skip-download" ] && SKIP=1

mkdir -p "$WORK" "$WORK/app"
jf_have unzip || jf_die "缺少 unzip"
[ -x "$JF_WINE_BIN" ] || jf_die "wine 不存在：$JF_WINE_BIN（用 JF_WINE_BIN 覆盖）"

if [ "$SKIP" = "0" ] || [ ! -f "$ZIP" ]; then
    jf_pass "下载 Windows 便携包（约 265MB）"
    if jf_have aria2c; then
        aria2c -c -x 8 -s 8 -k 4M --file-allocation=none -d "$WORK" -o "$(basename "$ZIP")" "$URL"
    else
        curl -4 -L --retry 3 -o "$ZIP" "$URL"
    fi
fi
[ -f "$ZIP" ] || jf_die "缺少 $ZIP"

if [ ! -d "$APP" ]; then
    jf_pass "解压"
    unzip -q "$ZIP" -d "$WORK/app"
fi

# wine 没有 Windows 系统 ICU；便携包本身不带 icuuc.dll，必须补
ICU_DIR="$WORK/icu"
if [ ! -f "$ICU_DIR/icuuc78.dll" ]; then
    jf_pass "下载 ICU（wine 专用，真机不需要）"
    mkdir -p "$ICU_DIR"
    curl -4 -L --retry 3 -o "$WORK/icu.zip" "$ICU_URL"
    unzip -q -j "$WORK/icu.zip" 'bin64/icuuc78.dll' 'bin64/icudt78.dll' -d "$ICU_DIR"
fi
cp "$ICU_DIR/icuuc78.dll" "$APP/_internal/PySide6/icuuc.dll"
cp "$ICU_DIR/icudt78.dll" "$APP/_internal/PySide6/icudt78.dll"

export WINEPREFIX="$JF_WINEPREFIX"
export WINEDEBUG="${WINEDEBUG:--all}"
mkdir -p "$WINEPREFIX"
[ -d "$WINEPREFIX/drive_c" ] || "$JF_WINE_BIN" wineboot -i
if [ ! -f "$WINEPREFIX/drive_c/windows/system32/msvcp140.dll" ]; then
    jf_pass "提示：prefix 缺 msvcp140.dll，首次请先跑：PATH=$(dirname "$JF_WINE_BIN"):\$PATH winetricks -q vcrun2022"
fi

DISP="$(jf_start_xvnc "$DISP")"
jf_start_wm "$DISP"

cd "$APP"
jf_pass "启动 JFLove.exe（DISPLAY=$DISP）"
DISPLAY="$DISP" LIBGL_ALWAYS_SOFTWARE=1 QT_OPENGL=software \
    QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --no-sandbox" \
    nohup "$JF_WINE_BIN" JFLove.exe >/tmp/jf-wine.log 2>&1 &
echo $! >/tmp/jf-wine.pid
sleep 30

DISPLAY="$DISP" ffmpeg -hide_banner -loglevel error \
    -f x11grab -video_size "$JF_XVNC_GEOMETRY" -i "$DISP" \
    -frames:v 1 -y "$OUT"
jf_pass "截图：$OUT"
jf_pass "日志：/tmp/jf-wine.log（含 GLES 降级告警属正常）"
