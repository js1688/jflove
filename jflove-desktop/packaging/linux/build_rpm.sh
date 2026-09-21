#!/bin/sh
# JFLove 桌面端 Fedora RPM 构建脚本（容器内外统一入口）
#
# 为什么要 Linux：PyInstaller 不能交叉编译。Windows/macOS 上跑不出 ELF，
# RPM 必须在 Linux（推荐 Fedora）主机上构建。
#
# 用法（在 Fedora 主机上，仓库任意位置）：
#   sh jflove-desktop/packaging/linux/build_rpm.sh                  # 默认 Fedora 43 基线
#   sh jflove-desktop/packaging/linux/build_rpm.sh --fedora 44
#   sh jflove-desktop/packaging/linux/build_rpm.sh --container      # 用 docker 起 fedora:<N> 构建
#   sh jflove-desktop/packaging/linux/build_rpm.sh --clean          # 先清空 build/
#
# 产出：jflove-desktop/build/dist/jflove-desktop-<ver>-1.fc<N>.x86_64.rpm
#
# 关于 --fedora <N>：它决定 %dist 标签（产物名里的 -1.fc<N>）与容器基线。
#   在 Fedora 主机上直接跑时，二进制仍链接**主机**的 glibc；要拿到真正的
#   fc<N> glibc 基线，请用 --container（在 fedora:<N> 容器里构建）。
#
# 依赖（Fedora 主机上手工跑时）：
#   sudo dnf install -y python3 python3-pip rpm-build

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DESKTOP_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$DESKTOP_DIR/.." && pwd)

FEDORA=43
USE_CONTAINER=0
CLEAN=0

usage() {
    cat <<'EOF'
用法：sh build_rpm.sh [--fedora <N>] [--container] [--clean] [--no-container]

  --fedora <N>     Fedora 基线版本号（默认 43；决定产物名 -1.fc<N> 与容器镜像）
  --container      用 Docker 起 fedora:<N> 容器构建（需要 docker，推荐用于拿到真实基线）
  --clean          构建前清空 jflove-desktop/build/
  --no-container   内部使用：容器内的第二次调用，禁止再起容器
  -h, --help       显示本帮助
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --fedora)
            [ $# -ge 2 ] || { echo "错误：--fedora 缺少参数值" >&2; exit 2; }
            FEDORA="$2"; shift 2 ;;
        --fedora=*)
            FEDORA="${1#*=}"; shift ;;
        --container)
            USE_CONTAINER=1; shift ;;
        --no-container)
            USE_CONTAINER=0; shift ;;
        --clean)
            CLEAN=1; shift ;;
        -h|--help)
            usage; exit 0 ;;
        *)
            echo "错误：未知参数 $1" >&2; usage >&2; exit 2 ;;
    esac
done

case "$FEDORA" in
    ''|*[!0-9]*) echo "错误：--fedora 必须是数字（收到 '$FEDORA'）" >&2; exit 2 ;;
esac

echo "[rpm] JFLove 桌面端 RPM 构建：Fedora 基线 fc$FEDORA，项目根 $PROJECT_ROOT"

# ── 1) 可选：在 fedora:<N> 容器里重跑本脚本 ─────────────────────
if [ "$USE_CONTAINER" = "1" ]; then
    command -v docker >/dev/null 2>&1 || {
        echo "错误：未找到 docker（--container 需要 docker；也可直接在 Fedora 主机上跑本脚本）" >&2
        exit 1
    }
    IMAGE="jflove-rpm-build:fc$FEDORA"
    CLEAN_ARG=""
    if [ "$CLEAN" = "1" ]; then CLEAN_ARG="--clean"; fi
    echo "[rpm] 构建镜像 $IMAGE（Dockerfile.build）"
    docker build --build-arg "FEDORA_VERSION=$FEDORA" \
        -f "$SCRIPT_DIR/Dockerfile.build" -t "$IMAGE" "$SCRIPT_DIR"
    echo "[rpm] 在容器内构建（仓库挂载到 /work；产物由容器写入宿主机仓库，属主为 root）"
    # shellcheck disable=SC2086  # CLEAN_ARG 需要按词拆分（可为空）
    exec docker run --rm \
        -v "$PROJECT_ROOT:/work" -w /work \
        "$IMAGE" \
        sh jflove-desktop/packaging/linux/build_rpm.sh \
            --fedora "$FEDORA" --no-container $CLEAN_ARG
fi

# ── 2) 平台与工具检查（缺失时给出可执行的下一步，绝不静默跳过）──
if [ "$(uname -s)" != "Linux" ]; then
    cat >&2 <<EOF
错误：本脚本必须在 Linux 上运行（当前 $(uname -s)）。
  PyInstaller 不能交叉编译：ELF / RPM 只能在 Linux 上生成。两条正路：
    1) 在一台 Fedora 机器上跑本脚本（或加 --container 用 docker 起 fedora:$FEDORA）
    2) 交给 CI 的 Fedora 容器 job 构建
EOF
    exit 2
fi

need() {
    # $1=命令  $2=安装提示
    command -v "$1" >/dev/null 2>&1 || {
        echo "错误：缺少 $1 —— 请先执行：$2" >&2
        exit 1
    }
}

need python3 "sudo dnf install -y python3"
need rpmbuild "sudo dnf install -y rpm-build"
python3 -c "import venv" >/dev/null 2>&1 || {
    echo "错误：python3 缺少 venv 模块 —— 请先执行：sudo dnf install -y python3-pip" >&2
    exit 1
}
# Fedora 的 PEP 668（外部管理环境）限制：必须用 venv 装 pip 依赖。
# 容器内不复用宿主机的 venv-linux（venv 内含绝对路径 + 平台二进制，跨环境不兼容）。
if [ -f /.dockerenv ]; then
    VENV_DIR="$DESKTOP_DIR/build/rpm-venv"
else
    VENV_DIR="$DESKTOP_DIR/venv-linux"
fi
if [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "[rpm] 创建 venv：$VENV_DIR"
    python3 -m venv "$VENV_DIR" || {
        echo "错误：创建 venv 失败 —— Fedora 上需 sudo dnf install -y python3-pip" >&2
        exit 1
    }
fi
VENV_PY="$VENV_DIR/bin/python"
if ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
    echo "[rpm] venv 内缺少 pip，尝试 ensurepip"
    "$VENV_PY" -m ensurepip --upgrade >/dev/null 2>&1 || {
        echo "错误：venv 内无法安装 pip —— 请执行 sudo dnf install -y python3-pip" >&2
        exit 1
    }
fi
echo "[rpm] 安装 Python 依赖（requirements.txt）"
"$VENV_PY" -m pip install --upgrade pip >/dev/null
"$VENV_PY" -m pip install -r "$DESKTOP_DIR/requirements.txt"

# ── 3) 版本号（唯一真相：仓库根 version.json）────────────────────
VERSION=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
    "$PROJECT_ROOT/version.json" | head -n 1)
[ -n "$VERSION" ] || { echo "错误：未能从 $PROJECT_ROOT/version.json 解析版本号" >&2; exit 1; }
echo "[rpm] 版本号 v$VERSION（来自 version.json）"

# ── 4) 生成 onedir 载荷（复用模块 build.py：安全门禁 + 版本校验一并走）──
echo "[rpm] 生成 onedir 载荷（PyInstaller）"
CLEAN_FLAG=""
if [ "$CLEAN" = "1" ]; then CLEAN_FLAG="--clean"; fi
# shellcheck disable=SC2086  # CLEAN_FLAG 需要按词拆分（可为空）
( cd "$DESKTOP_DIR" && "$VENV_PY" build.py --mode onedir --no-installer --no-zip $CLEAN_FLAG )

PAYLOAD="$DESKTOP_DIR/build/dist/JFLove"
[ -d "$PAYLOAD" ] || { echo "错误：未找到 onedir 产物目录 $PAYLOAD" >&2; exit 1; }

# ── 5) 组装 rpmbuild 目录树 ─────────────────────────────────────
RPMBUILD_ROOT="$DESKTOP_DIR/build/rpmbuild"
echo "[rpm] 准备 rpmbuild 目录树：$RPMBUILD_ROOT"
rm -rf "$RPMBUILD_ROOT"
mkdir -p "$RPMBUILD_ROOT/BUILD" "$RPMBUILD_ROOT/RPMS" "$RPMBUILD_ROOT/SOURCES" \
         "$RPMBUILD_ROOT/SPECS" "$RPMBUILD_ROOT/SRPMS"

mkdir -p "$RPMBUILD_ROOT/SOURCES/JFLove"
cp -a "$PAYLOAD/." "$RPMBUILD_ROOT/SOURCES/JFLove/"

cp "$SCRIPT_DIR/jflove-desktop.spec" "$RPMBUILD_ROOT/SPECS/"
cp "$SCRIPT_DIR/jflove.wrapper" "$RPMBUILD_ROOT/SOURCES/"
cp "$SCRIPT_DIR/jflove.desktop" "$RPMBUILD_ROOT/SOURCES/"
cp "$SCRIPT_DIR/icons/jflove-256.png" "$RPMBUILD_ROOT/SOURCES/"
cp "$DESKTOP_DIR/README.md" "$RPMBUILD_ROOT/SOURCES/README.md"
cp "$PROJECT_ROOT/LICENSE" "$RPMBUILD_ROOT/SOURCES/LICENSE"
# metainfo 里的版本号/日期占位符在此替换（版本号仍然只有一个来源）
sed -e "s/@VERSION@/$VERSION/g" \
    -e "s/@DATE@/$(date -u +%Y-%m-%d)/g" \
    "$SCRIPT_DIR/jflove.metainfo.xml" > "$RPMBUILD_ROOT/SOURCES/jflove.metainfo.xml"

# ── 6) rpmbuild ─────────────────────────────────────────────────
echo "[rpm] rpmbuild -bb（Release 标签固定为 .fc$FEDORA）"
rpmbuild --define "_topdir $RPMBUILD_ROOT" \
         --define "jflove_version $VERSION" \
         --define "dist .fc$FEDORA" \
         -bb "$RPMBUILD_ROOT/SPECS/jflove-desktop.spec"

# ── 7) 产物归位 + 校验输出 ──────────────────────────────────────
RPM_FILE=$(find "$RPMBUILD_ROOT/RPMS" -name '*.rpm' -type f | head -n 1)
[ -n "$RPM_FILE" ] || { echo "错误：rpmbuild 未产出 rpm" >&2; exit 1; }
mkdir -p "$DESKTOP_DIR/build/dist"
cp "$RPM_FILE" "$DESKTOP_DIR/build/dist/"
OUT="$DESKTOP_DIR/build/dist/$(basename "$RPM_FILE")"

echo ""
echo "[rpm] === 产物 ==="
ls -l "$OUT"
if command -v sha256sum >/dev/null 2>&1; then
    echo "[rpm] sha256=$(sha256sum "$OUT" | cut -c1-16)..."
fi
echo "[rpm] === 包信息（rpm -qpi）==="
rpm -qpi "$OUT" || :
echo "[rpm] === 文件清单（rpm -qpl，仅前 30 行）==="
rpm -qpl "$OUT" | head -n 30 || :
echo ""
echo "[rpm] 完成。安装：sudo dnf install ./$(basename "$OUT")"
