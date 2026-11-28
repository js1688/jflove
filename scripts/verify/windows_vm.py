#!/usr/bin/env python3
"""Windows 虚拟机（KVM/libvirt）自动化通道：经 QEMU Guest Agent 执行命令、传文件。

为什么用 QGA 而不是 WinRM / OpenSSH：
  QGA 只依赖 virtio-serial 通道，Windows 侧装一个 MSI 即可；不开启系统可选功能、
  不要求客机联网，宿主机侧也不需要额外 Python 依赖（只用标准库调用 virsh）。

用法：
  python3 scripts/verify/windows_vm.py exec "ver"
  python3 scripts/verify/windows_vm.py exec --ps "Get-CimInstance Win32_OperatingSystem | Select Caption"
  python3 scripts/verify/windows_vm.py exec --timeout 300 "C:\\\\path\\\\tel.exe"
  python3 scripts/verify/windows_vm.py push ./setup.exe 'C:\\\\Users\\\\jf\\\\jflove-setup.exe'
  python3 scripts/verify/windows_vm.py pull 'C:\\\\provision.log' /tmp/provision.log
  python3 scripts/verify/windows_vm.py ip
  python3 scripts/verify/windows_vm.py wait --timeout 180

退出码：0 成功；1 通道/执行失败；2 参数错误；3 客机命令以非 0 退出。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time

VM = os.environ.get("JF_WIN_VM", "jflove-win10")


def _resolve_uri() -> str:
    """解析 libvirt 通道：优先显式环境变量，其次 system，再回落 session（无需 root）。

    背景：system 模式需 root 启守护进程，本机 sudo 要密码；session 模式在 /dev/kvm
    可读写时完全可用。历史上只看 system 就把「守护进程没起」误报成「没有靶机」。
    与 scripts/verify/libvirt_env.sh 保持同一判定顺序。
    """
    explicit = os.environ.get("JF_LIBVIRT_URI")
    if explicit:
        return explicit
    for uri in ("qemu:///system", "qemu:///session"):
        try:
            proc = subprocess.run(
                ["virsh", "-c", uri, "list", "--all"],
                capture_output=True, text=True, timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0:
            return uri
    return "qemu:///system"


VIRSH = ["virsh", "-c", _resolve_uri()]
#: QGA 单次消息大小受 virtio-serial 缓冲限制，256KB 分片实测稳定
CHUNK = 256 * 1024


class ChannelError(RuntimeError):
    """QGA 通道不可用（客机未就绪 / 未安装 guest agent）。"""


def _virsh(*args: str, timeout: int = 60) -> str:
    """调用 virsh 并返回 stdout；失败抛 ChannelError。"""
    proc = subprocess.run([*VIRSH, *args], capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise ChannelError((proc.stderr or proc.stdout).strip())
    return proc.stdout


def agent(payload: dict, timeout: int = 60) -> dict:
    """向 QGA 发一条命令（同步），返回解析后的结果。"""
    raw = _virsh("qemu-agent-command", VM, json.dumps(payload, ensure_ascii=False), timeout=timeout)
    return json.loads(raw)


def wait_agent(timeout: int = 120) -> bool:
    """等待 QGA 上线（开机/重启后调用）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            agent({"execute": "guest-ping"})
            return True
        except (ChannelError, json.JSONDecodeError):
            time.sleep(2)
    return False


def guest_exec(script: str, mode: str = "cmd", timeout: int = 120) -> tuple[int, str, str]:
    """在客机执行一条命令，返回 (exitcode, stdout, stderr)。"""
    if mode == "ps":
        exe = "powershell.exe"
        args = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
    else:
        exe = "cmd.exe"
        args = ["/c", script]
    pid = agent({
        "execute": "guest-exec",
        "arguments": {"path": exe, "arg": args, "capture-output": True},
    })["return"]["pid"]

    deadline = time.time() + timeout
    while time.time() < deadline:
        st = agent({"execute": "guest-exec-status", "arguments": {"pid": pid}})["return"]
        if st.get("exited"):
            out = base64.b64decode(st.get("out-data", "")).decode("utf-8", "replace")
            err = base64.b64decode(st.get("err-data", "")).decode("utf-8", "replace")
            return int(st.get("exitcode", 0)), out, err
        time.sleep(0.3)
    raise ChannelError(f"客机命令超时（>{timeout}s）")


def guest_write(host_path: str, guest_path: str) -> int:
    """把宿主机文件写入客机，返回字节数。"""
    size = os.path.getsize(host_path)
    h = agent({"execute": "guest-file-open",
               "arguments": {"path": guest_path, "mode": "wb"}})["return"]
    try:
        sent = 0
        with open(host_path, "rb") as fh:
            while True:
                block = fh.read(CHUNK)
                if not block:
                    break
                b64 = base64.b64encode(block).decode("ascii")
                agent({"execute": "guest-file-write",
                       "arguments": {"handle": h, "buf-b64": b64}}, timeout=120)
                sent += len(block)
        return sent
    finally:
        agent({"execute": "guest-file-close", "arguments": {"handle": h}})


def guest_read(guest_path: str, host_path: str) -> int:
    """把客机文件读回宿主机，返回字节数。"""
    h = agent({"execute": "guest-file-open",
               "arguments": {"path": guest_path, "mode": "rb"}})["return"]
    total = 0
    try:
        with open(host_path, "wb") as fh:
            while True:
                res = agent({"execute": "guest-file-read",
                             "arguments": {"handle": h, "count": CHUNK}}, timeout=120)["return"]
                data = base64.b64decode(res.get("buf-b64", ""))
                if data:
                    fh.write(data)
                    total += len(data)
                if res.get("eof") or not data:
                    break
        return total
    finally:
        agent({"execute": "guest-file-close", "arguments": {"handle": h}})


def send_keys(*names: str, holdtime: int = 40) -> None:
    """向客机发送按键；多个键名会被同时按下（组合键语义）。"""
    if not names:
        return
    _virsh("send-key", VM, "--holdtime", str(holdtime), *names)


#: 无需 Shift 的字符 → libvirt 键名（send-key 用 Linux KEY_* 命名）
PLAIN_KEYS = {
    " ": "KEY_SPACE", "\n": "KEY_ENTER", "\t": "KEY_TAB",
    "-": "KEY_MINUS", "=": "KEY_EQUAL", "[": "KEY_LEFTBRACE", "]": "KEY_RIGHTBRACE",
    ";": "KEY_SEMICOLON", "'": "KEY_APOSTROPHE", "`": "KEY_GRAVE", "\\": "KEY_BACKSLASH",
    ",": "KEY_COMMA", ".": "KEY_DOT", "/": "KEY_SLASH",
}
#: 需要 Shift 的字符 → 基础键
SHIFT_KEYS = {
    "!": "KEY_1", "@": "KEY_2", "#": "KEY_3", "$": "KEY_4", "%": "KEY_5",
    "^": "KEY_6", "&": "KEY_7", "*": "KEY_8", "(": "KEY_9", ")": "KEY_0",
    "_": "KEY_MINUS", "+": "KEY_EQUAL", ":": "KEY_SEMICOLON", '"': "KEY_APOSTROPHE",
    "~": "KEY_GRAVE", "|": "KEY_BACKSLASH", "{": "KEY_LEFTBRACE", "}": "KEY_RIGHTBRACE",
    "<": "KEY_COMMA", ">": "KEY_DOT", "?": "KEY_SLASH",
}


def type_text(text: str, delay: float = 0.02) -> int:
    """把 ASCII 文本逐字符打进客机当前焦点窗口（不依赖 QGA/剪贴板）。"""
    sent = 0
    for ch in text:
        if ch.isascii() and ch.isalpha():
            base = f"KEY_{ch.upper()}"
            if ch.isupper():
                send_keys("KEY_LEFTSHIFT", base)
            else:
                send_keys(base)
        elif ch.isdigit():
            send_keys(f"KEY_{ch}")
        elif ch in PLAIN_KEYS:
            send_keys(PLAIN_KEYS[ch])
        elif ch in SHIFT_KEYS:
            send_keys("KEY_LEFTSHIFT", SHIFT_KEYS[ch])
        else:
            raise ValueError(f"不支持的字符：{ch!r}（仅可打印 ASCII 与 \\n\\t）")
        sent += 1
        time.sleep(delay)
    return sent


def guest_ip(timeout: float = 60.0) -> str:
    """
    取客机**非回环** IPv4 地址。

    QGA 刚上线时 `domifaddr --source agent` 常常还没有网卡信息（只写回环或完全空），
    早期版本直接取「第一行 ipv4」会拿到 `127.0.0.1`，后续所有 HTTP/CDP 动作都连到
    客机自己身上而失败，表现为「未取到客机 IP」或莫名其妙的超时。这里改为：

    - 轮询到超时为止，跳过 Loopback（父网卡名含 `Loopback` 或地址以 `127.` 开头）；
    - 优先返回非 `169.254.*`（链路本地）地址。

    :param timeout: 最长等待秒数（默认 60s）
    :returns: 客机 IPv4 字符串；超时返回空串
    """
    deadline = time.time() + timeout
    while True:
        out = _virsh("domifaddr", VM, "--source", "agent", "--full")
        candidates: list[str] = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 4 or parts[2] != "ipv4":
                continue
            # 回环条目没有 MAC 列，网卡名以 "Loopback" 开头
            if parts[0].startswith("Loopback"):
                continue
            addr = parts[3].split("/")[0]
            if addr.startswith("127.") or not addr:
                continue
            candidates.append(addr)
        if candidates:
            # 链路本地地址排到最后（它通常不是可用地址）
            candidates.sort(key=lambda a: a.startswith("169.254."))
            return candidates[0]
        if time.time() >= deadline:
            return ""
        time.sleep(3)


def main() -> int:
    """命令行入口。"""
    ap = argparse.ArgumentParser(description="Windows VM QGA 通道")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_exec = sub.add_parser("exec", help="在客机执行命令")
    p_exec.add_argument("script")
    p_exec.add_argument("--ps", action="store_true", help="用 PowerShell 执行")
    p_exec.add_argument("--timeout", type=int, default=120)

    p_push = sub.add_parser("push", help="宿主机 → 客机")
    p_push.add_argument("host")
    p_push.add_argument("guest")

    p_pull = sub.add_parser("pull", help="客机 → 宿主机")
    p_pull.add_argument("guest")
    p_pull.add_argument("host_out")

    p_wait = sub.add_parser("wait", help="等 QGA 上线")
    p_wait.add_argument("--timeout", type=int, default=120)

    sub.add_parser("ip", help="打印客机 IP")

    p_type = sub.add_parser("type", help="把文本打进客机焦点窗口")
    p_type.add_argument("text")
    p_type.add_argument("--delay", type=float, default=0.02)

    p_key = sub.add_parser("key", help="发送按键/组合键")
    p_key.add_argument("names", nargs="+")

    args = ap.parse_args()
    try:
        if args.cmd == "wait":
            ok = wait_agent(args.timeout)
            print("QGA ready" if ok else "QGA not ready")
            return 0 if ok else 1
        if args.cmd == "ip":
            ip = guest_ip()
            if not ip:
                print("未取到客机 IP", file=sys.stderr)
                return 1
            print(ip)
            return 0
        if args.cmd == "exec":
            code, out, err = guest_exec(args.script, "ps" if args.ps else "cmd", args.timeout)
            if out:
                print(out, end="" if out.endswith("\n") else "\n")
            if err:
                print(err, end="" if err.endswith("\n") else "\n", file=sys.stderr)
            return 0 if code == 0 else 3
        if args.cmd == "type":
            n = type_text(args.text, args.delay)
            print(f"typed {n} chars")
            return 0
        if args.cmd == "key":
            send_keys(*args.names)
            print(f"sent {len(args.names)} keys")
            return 0
        if args.cmd == "push":
            n = guest_write(args.host, args.guest)
            print(f"pushed {n} bytes -> {args.guest}")
            return 0
        if args.cmd == "pull":
            n = guest_read(args.guest, args.host_out)
            print(f"pulled {n} bytes -> {args.host_out}")
            return 0
    except ChannelError as exc:
        print(f"[windows_vm] 通道失败：{exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"[windows_vm] 文件不存在：{exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
