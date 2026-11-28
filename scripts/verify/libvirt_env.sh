#!/usr/bin/env bash
# libvirt 通道自动解析（source 使用，不直接执行）
#
# 背景（必读，别再踩）：
#   system 模式（qemu:///system）需要 root 启守护进程（sudo systemctl enable --now virtqemud.socket），
#   本机 sudo 要密码 → 历史上多次把「守护进程没起」误判成「没有靶机」而跳过 Windows 验证。
#   实测结论：只要 /dev/kvm 可读写，**session 模式（qemu:///session）不需要 root** 就能
#   define / start / stop / snapshot 整台 Windows VM；唯一前提是磁盘文件对本用户可读。
#
# 本文件职责：
#   1. 探测哪条通道可用，导出 JF_LIBVIRT_URI
#   2. session 通道下，若用户目录没有磁盘副本，自动从 system 盘 reflink 复制一份
#      （btrfs 秒级；原盘保持 root:600 不动，绝不删改）
#   3. 自动 define 域（缺 XML 时）
#
# 导出：
#   JF_LIBVIRT_URI   qemu:///system | qemu:///session
#   JF_WIN_MODE      system | session
#   JF_WIN_DISK      实际使用的磁盘路径
set -uo pipefail

JF_LIB_VM="${JF_LIB_VM:-${JF_WIN_VM:-jflove-win10}}"
JF_WIN_MODE="${JF_WIN_MODE:-auto}"
JF_SRC_DISK="${JF_WIN_SRC_DISK:-/var/lib/libvirt/images/${JF_LIB_VM}.qcow2}"
JF_WIN_DIR="${JF_WIN_DIR:-$HOME/vms/win10}"
JF_SESS_DISK="${JF_WIN_DISK:-$JF_WIN_DIR/${JF_LIB_VM}.qcow2}"

jf_lib_say()  { printf '[\033[36mlibvirt\033[0m] %s\n' "$*"; }
jf_lib_warn() { printf '[\033[33mlibvirt\033[0m] %s\n' "$*" >&2; }
jf_lib_err()  { printf '[\033[31mlibvirt\033[0m] %s\n' "$*" >&2; }

# 通道是否可用：能列出域即算可用（不要求已有域）
jf_uri_works() {
    virsh -c "$1" list --all >/dev/null 2>&1
}

# 选择通道，导出 JF_LIBVIRT_URI / JF_WIN_MODE
jf_select_uri() {
    if [ "$JF_WIN_MODE" = "system" ]; then
        JF_LIBVIRT_URI="qemu:///system"; JF_WIN_MODE="system"; export JF_LIBVIRT_URI JF_WIN_MODE; return 0
    fi
    if [ "$JF_WIN_MODE" = "session" ]; then
        JF_LIBVIRT_URI="qemu:///session"; JF_WIN_MODE="session"; export JF_LIBVIRT_URI JF_WIN_MODE; return 0
    fi
    # auto：system 优先（原设计），不可用则回落 session（无需 root）
    if jf_uri_works qemu:///system; then
        JF_LIBVIRT_URI="qemu:///system"; JF_WIN_MODE="system"
    elif [ -r /dev/kvm ] && [ -w /dev/kvm ] && jf_uri_works qemu:///session; then
        JF_LIBVIRT_URI="qemu:///session"; JF_WIN_MODE="session"
        jf_lib_warn "system 通道不可用（守护进程未起），已自动切到 session 模式（无需 root）"
    else
        jf_lib_err "两条通道都不可用：system 守护进程未起，session 也连不上"
        jf_lib_err "  system 修复：sudo systemctl enable --now virtqemud.socket"
        jf_lib_err "  session 前提：/dev/kvm 可读写（当前 $(ls -l /dev/kvm 2>&1 | awk '{print $1,$3,$4}')）"
        return 3
    fi
    export JF_LIBVIRT_URI JF_WIN_MODE
    return 0
}

# 包装 virsh，永远走已解析的通道
jf_vsh() { virsh -c "${JF_LIBVIRT_URI:-qemu:///system}" "$@"; }

# 该磁盘当前是否已被某个域占用（避免两个域同时写一个盘）
jf_disk_in_use_by() {
    local disk="$1" uri
    for uri in qemu:///system qemu:///session; do
        local out
        out="$(virsh -c "$uri" dumpxml --inactive "$JF_LIB_VM" 2>/dev/null || true)"
        if printf '%s' "$out" | grep -q -- "$disk"; then
            local st; st="$(virsh -c "$uri" domstate "$JF_LIB_VM" 2>/dev/null | head -1 || true)"
            case "$st" in 运行|running|paused) echo "$uri"; return 0 ;; esac
        fi
    done
    return 1
}

# session 模式：确保用户目录有可读的磁盘副本
# 1) 已有副本 → 直接用
# 2) 源盘本用户可读 → cp --reflink
# 3) 源盘 root-only → 用 docker（本机 daemon 为 root）在容器内 reflink 复制 + chown
jf_ensure_session_disk() {
    [ -f "$JF_SESS_DISK" ] && { JF_WIN_DISK="$JF_SESS_DISK"; export JF_WIN_DISK; return 0; }
    [ -f "$JF_SRC_DISK" ] || {
        jf_lib_err "找不到源盘：$JF_SRC_DISK"
        jf_lib_err "若确实没有靶机，才需要重建（用 ~/vms/win10/create-vm.sh 或 virt-install）；不要默认重建。"
        return 4
    }
    mkdir -p "$JF_WIN_DIR"
    jf_lib_say "首次使用 session 模式：从 $JF_SRC_DISK 生成用户副本（btrfs reflink，秒级，不改原盘）"
    if [ -r "$JF_SRC_DISK" ]; then
        cp --reflink=auto "$JF_SRC_DISK" "$JF_SESS_DISK" || return 5
    else
        # 原盘 root:600 → 借 docker（daemon 是 root）读并 reflink，再 chown 回当前用户
        command -v docker >/dev/null 2>&1 || {
            jf_lib_err "原盘不可读且无 docker，无法自动生成副本"
            return 5
        }
        local uid gid
        uid="$(id -u)"; gid="$(id -g)"
        docker run --rm --security-opt label=disable \
            -v "$(dirname "$JF_SRC_DISK")":/src:ro \
            -v "$JF_WIN_DIR":/dst:z \
            alpine:3.20 sh -c "cp --reflink=auto '/src/$(basename "$JF_SRC_DISK")' '/dst/$(basename "$JF_SESS_DISK")' && chown ${uid}:${gid} '/dst/$(basename "$JF_SESS_DISK")'" \
            || return 5
    fi
    chmod 600 "$JF_SESS_DISK" 2>/dev/null || true
    restorecon -F "$JF_SESS_DISK" 2>/dev/null || true
    jf_lib_say "副本就绪：$JF_SESS_DISK ($(du -h "$JF_SESS_DISK" | cut -f1))"
    JF_WIN_DISK="$JF_SESS_DISK"; export JF_WIN_DISK
    return 0
}

# session 模式：缺域则自动 define（指向用户副本）
jf_ensure_session_domain() {
    local st; st="$(jf_vsh domstate "$JF_LIB_VM" 2>/dev/null | head -1 | tr -d '[:space:]' || true)"
    [ -n "$st" ] && return 0
    local xml="$JF_WIN_DIR/${JF_LIB_VM}.xml"
    if [ ! -f "$xml" ]; then
        cat >"$xml" <<EOF
<domain type='kvm'>
  <name>${JF_LIB_VM}</name>
  <memory unit='MiB'>8192</memory>
  <vcpu>6</vcpu>
  <cpu mode='host-passthrough' check='none'/>
  <os><type arch='x86_64' machine='pc'>hvm</type><boot dev='hd'/></os>
  <features>
    <acpi/><apic/>
    <hyperv><relaxed state='on'/><vapic state='on'/><spinlocks state='on' retries='8191'/></hyperv>
  </features>
  <clock offset='localtime'><timer name='hypervclock' present='yes'/></clock>
  <devices>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2' cache='writeback' discard='unmap'/>
      <source file='${JF_WIN_DISK}'/>
      <target dev='sda' bus='sata'/>
    </disk>
    <graphics type='vnc' port='-1' autoport='yes' listen='127.0.0.1'/>
    <channel type='unix'><target type='virtio' name='org.qemu.guest_agent.0'/></channel>
    <interface type='user'><model type='e1000e'/></interface>
    <video><model type='virtio'/></video>
  </devices>
</domain>
EOF
    fi
    jf_lib_say "域未定义，自动 define（磁盘已存在，不重建系统）"
    jf_vsh define "$xml" >/dev/null 2>&1 || { jf_lib_err "define 失败：$xml"; return 5; }
    return 0
}

# 一次性准备：选通道 + （session 时）备盘/备域
jf_prepare_windows() {
    jf_select_uri || return 3
    if [ "$JF_WIN_MODE" = "session" ]; then
        jf_ensure_session_disk || return $?
        jf_ensure_session_domain || return $?
    fi
    export JF_WIN_DISK="${JF_WIN_DISK:-$JF_SESS_DISK}"
    return 0
}
