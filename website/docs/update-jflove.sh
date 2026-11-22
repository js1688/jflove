#!/usr/bin/env bash
#
# jflove 一键更新脚本（Linux）
#   作用：更新 jflove-server / jflove-web 两个容器；可选更新数据库文件
#   用法：bash update-jflove.sh
#
set -euo pipefail
trap 'printf "\033[1;31m[ERROR] 脚本在第 %s 行执行失败，已中止\033[0m\n" "$LINENO" >&2' ERR

# ===================== 可改配置 =====================
SERVER_CONTAINER="jflove-server"
WEB_CONTAINER="jflove-web"

SERVER_REPO="ghcr.io/js1688/jflove-server"
WEB_REPO="ghcr.io/js1688/jflove-web"

SERVER_PORT="8989"
WEB_PORT="18080"

DATA_DIR="/home/tanjun/jflove/data"   # 宿主数据目录 -> 容器 /data
STORAGE_DIR="/mnt"                    # 宿主存储目录 -> 容器 /storage

DB_NAME="jflove-prod.db"
DB_DIR="$DATA_DIR"                      # 数据库文件所在宿主目录（对应容器 /data）
DB_URL_BASE="https://raw.githubusercontent.com/js1688/jflove"   # 自动拼 v<版本号>
# ====================================================

ts()  { date '+%Y-%m-%d %H:%M:%S'; }
log() { printf '\033[1;34m[%s] %s\033[0m\n' "$(ts)" "$*"; }
ok()  { printf '\033[1;32m[OK] %s\033[0m\n' "$*"; }
warn(){ printf '\033[1;33m[WARN] %s\033[0m\n' "$*" >&2; }
die() { printf '\033[1;31m[ERROR] %s\033[0m\n' "$*" >&2; exit 1; }

# docker 权限自适应：当前用户跑不动就自动加 sudo
DOCKER="docker"
if ! docker ps >/dev/null 2>&1; then
  if command -v sudo >/dev/null 2>&1; then
    DOCKER="sudo docker"
  else
    die "docker 命令不可用，请检查 docker 服务与当前用户权限"
  fi
fi
$DOCKER version >/dev/null 2>&1 || die "docker 不可用，请确认 docker 已启动且有权限"

# 交互：ask_ver "提示"   版本号必填 —— 没有默认值，空值/格式非法一律重新询问
ask_ver() {
  local p="$1" a=""
  while :; do
    read -r -p "$(printf '\033[1;36m%s\033[0m（必填，无默认值）: ' "$p")" a || true
    a="${a// /}"; a="${a#v}"; a="${a#V}"
    if [[ -z "$a" ]]; then
      warn "版本号不能为空，必须手动输入（示例：1.6.1）"
      continue
    fi
    if [[ ! "$a" =~ ^[0-9][0-9A-Za-z._-]*$ ]]; then
      warn "版本号格式不合法：'$a'（示例：1.6.1）"
      continue
    fi
    printf '%s' "$a"; return
  done
}

# 交互：ask_yn "提示"   返回 y 或 n —— 必须明确输入，没有默认值
ask_yn() {
  local p="$1" a=""
  while :; do
    read -r -p "$(printf '\033[1;36m%s\033[0m [y/n，必填]: ' "$p")" a || true
    a="${a// /}"
    case "${a,,}" in
      y|yes|是) printf 'y'; return ;;
      n|no|否)  printf 'n'; return ;;
      *) warn "请输入 y 或 n（不能为空）" ;;
    esac
  done
}

stop_rm() {   # 停止并删除容器（不存在就跳过）
  local c="$1"
  if $DOCKER ps -a --format '{{.Names}}' | grep -qx "$c"; then
    log "停止并删除容器 $c"
    $DOCKER stop "$c" >/dev/null 2>&1 || true
    $DOCKER rm   "$c" >/dev/null 2>&1 || true
    ok "容器 $c 已停止并删除"
  else
    log "容器 $c 不存在，跳过"
  fi
}

rmi_old() {   # 删除旧镜像，失败不阻断
  [[ -n "$1" ]] || return 0
  if $DOCKER image inspect "$1" >/dev/null 2>&1; then
    $DOCKER rmi "$1" >/dev/null 2>&1 && ok "已删除旧镜像 $1" || warn "旧镜像 $1 删除失败（可能有其它容器占用），忽略"
  fi
}

echo
log "当前容器状态："
$DOCKER ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' || true
echo "-----------------------------------------------------------------"

# 记下正在运行的镜像（只用于展示，以及后面删除旧镜像）
OLD_SERVER_IMG=$($DOCKER inspect -f '{{.Config.Image}}' "$SERVER_CONTAINER" 2>/dev/null || true)
OLD_WEB_IMG=$($DOCKER inspect -f '{{.Config.Image}}' "$WEB_CONTAINER" 2>/dev/null || true)
if [[ -n "$OLD_SERVER_IMG" ]]; then CUR_SERVER_VER="${OLD_SERVER_IMG##*:}"; else CUR_SERVER_VER="未运行"; fi
if [[ -n "$OLD_WEB_IMG"    ]]; then CUR_WEB_VER="${OLD_WEB_IMG##*:}";    else CUR_WEB_VER="未运行";    fi

# ---------- 1. 询问版本号（必填，不给默认值）----------
WEB_VER="$(ask_ver '请输入 jflove-web 版本号（当前 '"$CUR_WEB_VER"'）')"
SERVER_VER="$(ask_ver '请输入 jflove-server 版本号（当前 '"$CUR_SERVER_VER"'）')"

# ---------- 2. 询问是否更新数据库 ----------
UPDATE_DB="$(ask_yn "是否需要更新数据库（$DB_NAME）")"
DB_VER=""
if [[ "$UPDATE_DB" == "y" ]]; then
  DB_VER="$(ask_ver '请输入数据库文件所在的 git tag 版本号（将自动拼成 v<版本号>）')"
fi

SERVER_IMAGE="${SERVER_REPO}:${SERVER_VER}"
WEB_IMAGE="${WEB_REPO}:${WEB_VER}"
TMP_DB=""

echo
echo "=================== 本次更新计划 ==================="
printf '  jflove-server : %s -> %s\n' "$CUR_SERVER_VER" "$SERVER_VER"
printf '  jflove-web    : %s -> %s\n' "$CUR_WEB_VER"    "$WEB_VER"
if [[ "$UPDATE_DB" == "y" ]]; then
  printf '  数据库        : 更新（v%s）-> %s/%s\n' "$DB_VER" "$DB_DIR" "$DB_NAME"
else
  printf '  数据库        : 不更新\n'
fi
printf '  宿主目录      : %s:/data , %s:/storage\n' "$DATA_DIR" "$STORAGE_DIR"
echo "===================================================="
[[ "$(ask_yn '确认开始更新？')" == "y" ]] || { warn "已取消，未做任何改动"; exit 0; }

# ---------- 3. 先下载并校验数据库（此时服务还在跑，只落临时文件，不影响线上）----------
if [[ "$UPDATE_DB" == "y" ]]; then
  mkdir -p "$DB_DIR"
  TMP_DB="${DB_DIR}/${DB_NAME}.new.$$"
  DB_URL="${DB_URL_BASE}/v${DB_VER}/jflove-db/${DB_NAME}"
  log "下载数据库：$DB_URL"
  command -v wget >/dev/null 2>&1 || die "未找到 wget，请先安装或改用 curl"
  if ! wget -O "$TMP_DB" "$DB_URL"; then
    rm -f "$TMP_DB"; TMP_DB=""
    die "数据库下载失败（请确认 tag v${DB_VER} 是否存在），已中止，线上服务未受影响"
  fi
  SIZE=$(stat -c %s "$TMP_DB")
  if (( SIZE <= 1024 )) || head -c 256 "$TMP_DB" | grep -qi 'not found'; then
    rm -f "$TMP_DB"; TMP_DB=""
    die "下载内容异常（大小 ${SIZE} 字节，疑似 404 页面），已中止，线上服务未受影响"
  fi
  ok "数据库下载完成：${SIZE} 字节（待停服后替换）"
fi

# ---------- 4. 先拉取新镜像，拉不到就不动线上 ----------
log "拉取新镜像：$SERVER_IMAGE"
$DOCKER pull "$SERVER_IMAGE" || die "拉取 $SERVER_IMAGE 失败（请确认版本号），已中止，线上服务未受影响"
log "拉取新镜像：$WEB_IMAGE"
$DOCKER pull "$WEB_IMAGE"    || die "拉取 $WEB_IMAGE 失败（请确认版本号），已中止，线上服务未受影响"
ok "新镜像已就绪"

# ---------- 5. 停止并删除容器 ----------
stop_rm "$SERVER_CONTAINER"
stop_rm "$WEB_CONTAINER"

# ---------- 6. 替换数据库文件（已停服，安全替换 + 备份）----------
if [[ "$UPDATE_DB" == "y" ]]; then
  if [[ -f "${DB_DIR}/${DB_NAME}" ]]; then
    BAK="${DB_DIR}/${DB_NAME}.bak.$(date +%Y%m%d_%H%M%S)"
    cp -a "${DB_DIR}/${DB_NAME}" "$BAK"
    ok "旧数据库已备份：$BAK"
    DB_BAK_SHOWN="$BAK"
  fi
  mv -f "$TMP_DB" "${DB_DIR}/${DB_NAME}"
  TMP_DB=""
  # 若容器内以非 root 运行且启动报权限错误，可放开下面这行（按实际 uid:gid 改）
  # chown 1000:1000 "${DB_DIR}/${DB_NAME}"
  ok "数据库已更新：${DB_DIR}/${DB_NAME}"
fi

# ---------- 7. 删除旧镜像 ----------
if [[ -n "$OLD_SERVER_IMG" && "$OLD_SERVER_IMG" != "$SERVER_IMAGE" ]]; then rmi_old "$OLD_SERVER_IMG"; fi
if [[ -n "$OLD_WEB_IMG"    && "$OLD_WEB_IMG"    != "$WEB_IMAGE"    ]]; then rmi_old "$OLD_WEB_IMG";    fi

# ---------- 8. 重新启动 ----------
log "启动 jflove-server"
$DOCKER run -d --name "$SERVER_CONTAINER" \
  -p "${SERVER_PORT}:${SERVER_PORT}" \
  -v "${DATA_DIR}:/data" \
  -v "${STORAGE_DIR}:/storage" \
  --restart=always \
  "$SERVER_IMAGE"

log "启动 jflove-web"
$DOCKER run -d --name "$WEB_CONTAINER" \
  -p "${WEB_PORT}:80" \
  --restart=always \
  "$WEB_IMAGE"

sleep 3
echo
log "更新完成，当前状态："
$DOCKER ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' || true
echo
ok "jflove-server -> $SERVER_VER   |   jflove-web -> $WEB_VER   |   数据库：$([[ "$UPDATE_DB" == "y" ]] && echo "已更新(v$DB_VER)" || echo "未更新")"
[[ -n "${DB_BAK_SHOWN:-}" ]] && echo "     旧库备份：$DB_BAK_SHOWN（回滚：docker stop ${SERVER_CONTAINER} && mv $DB_BAK_SHOWN ${DB_DIR}/${DB_NAME} && docker start ${SERVER_CONTAINER}）"
echo "     查看日志：$DOCKER logs -f ${SERVER_CONTAINER}   /   $DOCKER logs -f ${WEB_CONTAINER}"
echo "     清理悬空镜像（可选）：$DOCKER image prune -f"
exit 0
