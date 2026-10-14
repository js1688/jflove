"""
R-2 复验：`QLocalServer.listen()` 在 Windows 上是否对**同名**监听做仲裁？

背景：设计 §6.6.1 原定「用 `listen()` 的返回值做唯一仲裁」。`src/utils/single_instance.py`
的模块文档声称本机实测证明该仲裁在 Windows 上不成立（第二个同名 `listen()` 仍返回 True、
`serverError()` 成功/失败同值），并据此把仲裁者换成「连接探测优先 + 自探测」。
**那份证据在本仓库不存在**（编排者 2026-10-14 核实），故本脚本独立复现该结论。

决定性判据（不需要连接）：
  * A 进程对 NAME 调 `listen()` 并保持存活；
  * B 进程在 A 存活期间对**同一 NAME** 调 `listen()`：
      - B 返回 False（AddressInUse）⇒ **仲裁成立** ⇒ 设计原口径可用，实现里的自探测属过度设计；
      - B 返回 True 且 `errorString()` 为空（成功/失败同值）⇒ **仲裁不成立** ⇒ 该设计修订成立。

附加观测：C 进程连接 NAME，看 A 是否收到它的数据（说明 A 仍是最先的监听者）。

用法（工作目录 `jflove-desktop`）：
    .\\venv-win\\Scripts\\python.exe poc\\editor-probes\\r2-local-server-arbitration.py

产物：`out/r2-arbitration.json` + `out/r2-arbitration.log`（本脚本是 `single_instance.py`
文档所引证据的**可复现来源**）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)
NAME = "jflove-r2-probe-arbitration"


def _server_state(name: str) -> dict:
    """对 NAME 尝试 listen()，返回可比较的状态（子进程内调用）"""
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtNetwork import QLocalServer

    app = QCoreApplication([])  # noqa: F841 - 必须有应用实例
    server = QLocalServer()
    ok = bool(server.listen(name))
    state = {
        "listen_ok": ok,
        "is_listening": bool(server.isListening()),
        "error_string": server.errorString(),
        "error_code": str(server.serverError()),
    }
    time.sleep(0.4)  # 保持存活，让父进程观测"两个监听者并存"
    server.close()
    return state


def _child(name: str, out_file: str) -> int:
    """子进程入口：把状态写进文件（避免依赖 stdout 编码/缓冲）"""
    try:
        state = _server_state(name)
    except Exception as exc:  # 子进程异常也要留证
        state = {"exception": f"{type(exc).__name__}: {exc}"}
    Path(out_file).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return 0


def _connect_probe(name: str, out_file: str) -> int:
    """子进程入口：连接 NAME 并写一行，报告写入结果"""
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtNetwork import QLocalSocket

    app = QCoreApplication([])  # noqa: F841
    sock = QLocalSocket()
    sock.connectToServer(name)
    connected = bool(sock.waitForConnected(1500))
    written = -1
    if connected:
        written = int(sock.write(b"R2 from-C\n"))
        sock.flush()
        sock.waitForBytesWritten(500)
        time.sleep(0.2)
    sock.abort()
    Path(out_file).write_text(
        json.dumps({"connected": connected, "written": written}, ensure_ascii=False),
        encoding="utf-8",
    )
    return 0


def main() -> int:
    if len(sys.argv) > 3 and sys.argv[1] == "--child":
        return _child(sys.argv[2], sys.argv[3])
    if len(sys.argv) > 3 and sys.argv[1] == "--connect":
        return _connect_probe(sys.argv[2], sys.argv[3])

    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
    from PySide6.QtNetwork import QLocalServer

    script = str(Path(__file__).resolve())
    exe = sys.executable
    log_lines: list[str] = []
    report: dict = {"name": NAME, "python": sys.version.split()[0]}

    def log(line: str) -> None:
        log_lines.append(line)
        print(line.encode("utf-8", "replace").decode("utf-8", "replace"), flush=True)

    QLocalServer.removeServer(NAME)
    app = QCoreApplication([])
    server_a = QLocalServer()
    received: list[str] = []

    def on_connection() -> None:
        while server_a.hasPendingConnections():
            conn = server_a.nextPendingConnection()
            if conn is None:
                continue
            buf = b""
            deadline = time.monotonic() + 1.0
            while b"\n" not in buf and time.monotonic() < deadline:
                if conn.waitForReadyRead(200) and conn.bytesAvailable():
                    buf += bytes(conn.readAll())
            received.append(buf.decode("utf-8", "replace").strip())
            conn.disconnectFromServer()

    server_a.newConnection.connect(on_connection)
    ok_a = bool(server_a.listen(NAME))
    report["A"] = {
        "listen_ok": ok_a,
        "is_listening": bool(server_a.isListening()),
        "error_string": server_a.errorString(),
        "error_code": str(server_a.serverError()),
    }
    log(f"R2 A: {json.dumps(report['A'], ensure_ascii=False)}")

    with tempfile.TemporaryDirectory() as tmp:
        b_file = str(Path(tmp) / "b.json")
        proc_b = subprocess.run([exe, script, "--child", NAME, b_file],
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace")
        report["B"] = json.loads(Path(b_file).read_text(encoding="utf-8")) if Path(b_file).exists() else None
        log(f"R2 B: {json.dumps(report['B'], ensure_ascii=False)}")
        if proc_b.returncode != 0 or not Path(b_file).exists():
            log(f"R2 B stderr: {(proc_b.stderr or '')[-400:]}")

        # 让 A 的事件循环有机会处理连接（自探测语义的等价观测）
        loop = QEventLoop()
        QTimer.singleShot(300, loop.quit)
        loop.exec()

        c_file = str(Path(tmp) / "c.json")
        proc_c = subprocess.run([exe, script, "--connect", NAME, c_file],
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace")
        report["C"] = json.loads(Path(c_file).read_text(encoding="utf-8")) if Path(c_file).exists() else None
        log(f"R2 C: {json.dumps(report['C'], ensure_ascii=False)}")
        if proc_c.returncode != 0 or not Path(c_file).exists():
            log(f"R2 C stderr: {(proc_c.stderr or '')[-400:]}")

        loop2 = QEventLoop()
        QTimer.singleShot(600, loop2.quit)
        loop2.exec()

    report["A_received"] = received
    log(f"R2 A_received: {received}")

    b = report.get("B") or {}
    b_listen = bool(b.get("listen_ok"))
    report["verdict"] = (
        "仲裁不成立（同名 listen 仍返回 True）" if b_listen
        else "仲裁成立（同名 listen 返回 False）"
    )
    report["design_revision_supported"] = b_listen
    log(f"R2 结论: {report['verdict']}；"
        f"B.listen={b_listen} B.error={b.get('error_string')!r} "
        f"C.connected={(report.get('C') or {}).get('connected')} A_received={received}")

    server_a.close()
    (OUT_DIR / "r2-arbitration.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUT_DIR / "r2-arbitration.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    log(f"R2 证据已写入 {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
