#!/usr/bin/env bash
#
# 官网本地验证（一条命令跑完全部可判定检查）
#
# 为什么要有这个脚本：
#   "构建 → 起容器 → 逐个路由 curl → 断言下载链接/面包屑 → 双视口截图"
#   这套动作此前每次更新官网都要手工敲一遍，容易漏（漏了就等于没验）。
#   固化成脚本后，退出码即判据：0 = 全部通过，非 0 = 有项没过。
#
# 用法：
#   bash website/scripts/verify-site.sh              # 全流程（构建镜像 + 起容器 + 验证）
#   bash website/scripts/verify-site.sh --no-build   # 用已有镜像
#   OUT=/tmp/my-shots bash website/scripts/verify-site.sh
#
# 判据（任一失败即非 0 退出）：
#   ① 版本号一致（sync_version --check）
#   ② 内容门禁 14 条（check_website_sync）
#   ③ 镜像构建成功
#   ④ 9 条路由全部 200（含 /install 重定向到 /download）
#   ⑤ 下载页每个「立即下载」指向 GitHub Release 真实资产
#   ⑥ 面包屑「产品」是真链接
#   ⑦ 双视口截图产出且尺寸正确
#
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

IMAGE="${IMAGE:-jflove-website:local-verify}"
OUT="${OUT:-/tmp/jf-website-verify}"
PY="${PY:-$ROOT/jflove-server/venv-linux/bin/python}"
BUILD=1
[ "${1:-}" = "--no-build" ] && BUILD=0

PASS=0
FAIL=0
FAILED_ITEMS=()

ts()  { date '+%H:%M:%S'; }
note() { printf '\033[1;34m[%s] %s\033[0m\n' "$(ts)" "$*"; }
pass() { printf '\033[1;32m  [PASS] %s\033[0m\n' "$*"; PASS=$((PASS+1)); }
fail() { printf '\033[1;31m  [FAIL] %s\033[0m\n' "$*"; FAIL=$((FAIL+1)); FAILED_ITEMS+=("$*"); }
warn() { printf '\033[1;33m  [WARN] %s\033[0m\n' "$*"; }

mkdir -p "$OUT"
CONTAINER="jf-site-verify-$$"
PORT=""
cleanup() {
  if [ -n "$CONTAINER" ] && docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    docker stop "$CONTAINER" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# ── ① 版本号 ──────────────────────────────────────────────
note "① 版本号一致性"
if "$PY" scripts/sync_version.py --check >/tmp/jf-ver.log 2>&1; then
  pass "版本号全部一致（$(grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' /tmp/jf-ver.log | head -1)）"
else
  fail "版本号不一致：$(tail -2 /tmp/jf-ver.log | tr '\n' ' ')"
fi

# ── ② 内容门禁 ────────────────────────────────────────────
note "② 内容门禁（14 条）"
if "$PY" scripts/check_website_sync.py >/tmp/jf-gate.log 2>&1; then
  pass "$(grep -oE '（[0-9]+/[0-9]+ 条判据' /tmp/jf-gate.log | tr -d '（' | head -1) 通过"
else
  fail "门禁未通过：$(grep -E '\[FAIL\]' /tmp/jf-gate.log | head -3 | tr '\n' ' ')"
fi

# ── ③ 构建镜像 ────────────────────────────────────────────
if [ "$BUILD" = "1" ]; then
  note "③ 构建镜像 $IMAGE"
  if docker build -q -f website/Dockerfile -t "$IMAGE" . >/tmp/jf-build.log 2>&1; then
    pass "镜像构建成功（$(docker images "$IMAGE" --format '{{.Size}}' | head -1)）"
  else
    fail "镜像构建失败：$(tail -5 /tmp/jf-build.log | tr '\n' ' ')"
    echo; echo "构建失败，后续检查跳过"; exit 1
  fi
else
  note "③ 跳过构建（--no-build）"
  docker image inspect "$IMAGE" >/dev/null 2>&1 && pass "已有镜像可用" || { fail "镜像不存在：$IMAGE"; exit 1; }
fi

# ── 起容器（自动挑空闲端口）────────────────────────────────
for candidate in 18091 18092 18093 18094 18095; do
  if ! ss -lptn "sport = :$candidate" 2>/dev/null | grep -q LISTEN; then
    PORT="$candidate"; break
  fi
done
[ -z "$PORT" ] && { fail "找不到空闲端口"; exit 1; }

note "起容器（端口 $PORT）"
if ! docker run -d --name "$CONTAINER" -p "$PORT:80" "$IMAGE" >/dev/null 2>&1; then
  fail "容器启动失败"; exit 1
fi
BASE="http://127.0.0.1:$PORT"
for _ in $(seq 1 30); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/" 2>/dev/null)" = "200" ] && break
  sleep 1
done

# ── ④ 逐路由 ──────────────────────────────────────────────
note "④ 路由可达性"
ROUTES="/ /features /docs /api /api-announcements /download /install /guide/web /guide/desktop /guide/mobile /security"
BAD=""
for r in $ROUTES; do
  code="$(curl -s -o /dev/null -w '%{http_code}' "$BASE$r")"
  [ "$code" != "200" ] && BAD="$BAD $r($code)"
done
if [ -z "$BAD" ]; then
  pass "全部 $(echo $ROUTES | wc -w) 条路由 200"
else
  fail "以下路由非 200：$BAD"
fi

# /install 必须**在浏览器里**落到 /download（<Navigate> 是客户端重定向，curl 看不到）
INSTALL_PATH="$(node scripts/verify/web_shot.mjs --url "$BASE/install" \
  --eval "location.pathname" \
  --out "$OUT/_install-check.png" --width 1200 --height 500 --wait 2500 --port 9706 2>/dev/null \
  | grep -oE 'eval: "[^"]*"' | sed -E 's/eval: "([^"]*)"/\1/' | head -1)"
if [ "$INSTALL_PATH" = "/download" ]; then
  pass "/install 已重定向到 /download"
else
  fail "/install 未落到下载页（实际 pathname=$INSTALL_PATH）"
fi

# ── ⑤ 下载链接必须是真实 Release 资产 ───────────────────────
note "⑤ 下载链接断言"
# 返回 "总数|非Release直链数"，避免 JSON 转义造成的误判
DL_RESULT="$(node scripts/verify/web_shot.mjs --url "$BASE/download" \
  --eval "(() => { const as=[...document.querySelectorAll('a')].filter(a=>(a.textContent||'').includes('立即下载')); const bad=as.filter(a=>{const h=a.getAttribute('href')||''; return !h.includes('github.com/js1688/jflove/releases/download/');}); return as.length + '|' + bad.length; })()" \
  --out "$OUT/_dl-check.png" --width 1440 --height 600 --wait 2500 --port 9701 2>/dev/null \
  | grep -oE 'eval: "[0-9]+\|[0-9]+"' | grep -oE '[0-9]+\|[0-9]+' | head -1)"
LINK_COUNT="${DL_RESULT%%|*}"
NON_GH="${DL_RESULT##*|}"
if [ -n "$LINK_COUNT" ] && [ "$LINK_COUNT" -ge 4 ] && [ "$NON_GH" = "0" ]; then
  pass "$LINK_COUNT 个下载按钮全部指向 GitHub Release 真实资产"
else
  fail "下载链接异常（结果=$DL_RESULT，期望 总数>=4 且 非Release=0）"
fi

# ── ⑥ 面包屑可点 ──────────────────────────────────────────
note "⑥ 面包屑可点击"
# 返回面包屑里可点链接的数量（数字），避开 JSON 引号转义
CRUMB_N="$(node scripts/verify/web_shot.mjs --url "$BASE/features" \
  --eval "(() => { const n=document.querySelector('#breadcrumb'); if(!n) return -1; return [...n.querySelectorAll('a')].filter(a=>a.getAttribute('href')).length; })()" \
  --out "$OUT/_crumb-check.png" --width 1440 --height 400 --wait 2000 --port 9702 2>/dev/null \
  | grep -oE 'eval: .*' | sed -E 's/^eval: //' | tr -d '"' | head -1)"
case "$CRUMB_N" in
  ''|*[!0-9-]*) CRUMB_N_VALID=0 ;;
  *) CRUMB_N_VALID=1 ;;
esac
if [ "$CRUMB_N_VALID" = "1" ] && [ "$CRUMB_N" -ge 1 ] 2>/dev/null; then
  pass "面包屑含 $CRUMB_N 个可点链接"
else
  fail "面包屑没有可点链接（结果=$CRUMB_N，-1 表示找不到 nav）"
fi

# ── ⑦ 双视口截图 ──────────────────────────────────────────
note "⑦ 双视口截图"
node scripts/verify/web_shot.mjs --url "$BASE/" --out "$OUT/home-desktop.png" \
  --width 1440 --height 900 --wait 2500 --port 9703 2>/dev/null
node scripts/verify/web_shot.mjs --url "$BASE/" --out "$OUT/home-mobile.png" \
  --width 390 --height 844 --mobile --wait 2500 --port 9704 2>/dev/null
node scripts/verify/web_shot.mjs --url "$BASE/download" --out "$OUT/download.png" \
  --width 1440 --height 1200 --wait 2500 --port 9705 2>/dev/null

SHOT_OK=1
for f in home-desktop.png home-mobile.png download.png; do
  [ -s "$OUT/$f" ] || SHOT_OK=0
done
if [ "$SHOT_OK" = "1" ]; then
  pass "3 张截图已产出 → $OUT"
else
  fail "截图缺失（见 $OUT）"
fi

# ── 汇总 ──────────────────────────────────────────────────
echo
echo "════════════════════════════════════════════"
if [ "$FAIL" = "0" ]; then
  printf '\033[1;32m结果：全部通过（%d 项）\033[0m\n' "$PASS"
  echo "  截图目录：$OUT"
  echo "  本地预览：$BASE （容器验证完已停止）"
  exit 0
else
  printf '\033[1;31m结果：%d 项通过，%d 项失败\033[0m\n' "$PASS" "$FAIL"
  for item in "${FAILED_ITEMS[@]}"; do echo "  - $item"; done
  exit 1
fi
