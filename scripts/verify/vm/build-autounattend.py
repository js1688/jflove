#!/usr/bin/env python3
"""把 setup-guest.ps1 内嵌进 autounattend.xml，并打包成 1.44MB 软盘镜像。

为什么用软盘镜像装载应答文件：
  Windows Setup 只在"可移动介质根目录"和安装介质根目录找 autounattend.xml，
  软驱固定是 A:，不依赖 CD-ROM 盘符顺序，是最确定的一条路。

用法：python3 build-autounattend.py
产出：autounattend.xml（人可读）+ autounattend.img（挂给 virt-install 的 floppy）
"""

from __future__ import annotations

import base64
import pathlib
import subprocess

HERE = pathlib.Path(__file__).resolve().parent
PS1 = HERE / "setup-guest.ps1"
TEMPLATE = HERE / "autounattend.template.xml"
XML_OUT = HERE / "autounattend.xml"
IMG_OUT = HERE / "autounattend.img"
#: specialize 阶段由 SYSTEM 执行；路径必须短（命令行 8191 上限），故走软盘 A:
PROVISION_CMD = r"powershell.exe -NoProfile -ExecutionPolicy Bypass -File A:\setup-guest.ps1"


def main() -> int:
    """生成应答文件与软盘镜像；任一步失败即非 0 退出。"""
    xml = TEMPLATE.read_text(encoding="utf-8").replace("__PROVISION_CMD__", PROVISION_CMD)
    XML_OUT.write_text(xml, encoding="utf-8")
    print(f"[ok] {XML_OUT.name}  {XML_OUT.stat().st_size} 字节（provision 命令 {len(PROVISION_CMD)} 字符）")

    # 1.44MB FAT12 软盘镜像
    subprocess.run(
        ["dd", "if=/dev/zero", f"of={IMG_OUT}", "bs=1k", "count=1440"], check=True, capture_output=True
    )
    subprocess.run(["mkfs.vfat", "-F", "12", "-n", "ATTUNAD", str(IMG_OUT)], check=True, capture_output=True)
    subprocess.run(["mcopy", "-i", str(IMG_OUT), str(XML_OUT), "::/autounattend.xml"], check=True)
    # 脚本必须带 BOM 落盘：PowerShell 5.1 无 BOM 时按 ANSI(GBK) 读，中文注释会乱码
    ps1_utf8bom = HERE / "setup-guest.bom.ps1"
    ps1_utf8bom.write_text("\ufeff" + PS1.read_text(encoding="utf-8"), encoding="utf-8")
    subprocess.run(["mcopy", "-i", str(IMG_OUT), str(ps1_utf8bom), "::/setup-guest.ps1"], check=True)
    listing = subprocess.run(["mdir", "-i", str(IMG_OUT), "::/"], capture_output=True, text=True, check=True)
    print(f"[ok] {IMG_OUT.name}\n{listing.stdout}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
