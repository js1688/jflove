#!/usr/bin/env bash
# 一次性创建 jflove 自动化测试用 Windows 10 VM（libvirt system 域）
# 之后所有日常启停走 scripts/verify/vm-win.sh，不再需要本脚本。
set -euo pipefail

NAME="${JF_WIN_VM:-jflove-win10}"
DISK=/var/lib/libvirt/images/${NAME}.qcow2
ISO_WIN=/var/lib/libvirt/images/win10-22h2.iso
ISO_VIRTIO=/var/lib/libvirt/images/virtio-win.iso
FLOPPY=/var/lib/libvirt/images/autounattend.img

vir="sg libvirt -c"

$vir "virsh -c qemu:///system net-start default" 2>/dev/null || true
$vir "virsh -c qemu:///system net-autostart default"

# 只清系统盘：--remove-all-storage 会把挂载的 ISO/软盘镜像一并删掉
$vir "virsh -c qemu:///system destroy ${NAME}" 2>/dev/null || true
$vir "virsh -c qemu:///system undefine ${NAME} --nvram" 2>/dev/null || true
rm -f ${DISK} 2>/dev/null || true

$vir "virt-install \
  --connect qemu:///system \
  --name ${NAME} \
  --osinfo win10 \
  --machine pc \
  --cpu host-passthrough \
  --memory 8192 \
  --vcpus 6,sockets=1,cores=6,threads=1 \
  --features hyperv_relaxed=on,hyperv_vapic=on,hyperv_spinlocks=on,hyperv_spinlocks_retries=8191 \
  --clock offset=localtime,hpet_present=no,hypervclock_present=yes \
  --disk path=${DISK},size=64,format=qcow2,bus=sata,cache=writeback,discard=unmap \
  --disk path=${FLOPPY},device=floppy,format=raw,readonly=on \
  --disk path=${ISO_VIRTIO},device=cdrom,format=raw,readonly=on \
  --cdrom ${ISO_WIN} \
  --network network=default,model=e1000e \
  --graphics spice,listen=127.0.0.1 \
  --video virtio \
  --channel unix,name=org.qemu.guest_agent.0 \
  --boot cdrom,hd \
  --noautoconsole"

$vir "virsh -c qemu:///system autostart --disable ${NAME}"
echo "[ok] VM ${NAME} 已创建（开机不自启）"
