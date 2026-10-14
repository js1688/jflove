"""
R-3 复验：单实例守卫的**真实跨进程**往返（不是替身！）

单测（`tests/test_single_instance_v1_6_0.py`）用 `QLocalServer`/`QLocalSocket` 替身覆盖逻辑分支，
但"第二实例是否真的能找到首实例并把路径交到它手里"只能在**真进程间**验证（AC-46）。

本脚本三个角色：
  A（首实例）  : 真的 `listen()`，UI 就绪后注入 sink，收路径写证据文件
  B（第二实例）: 判定应为"非首实例"（不许 listen），把路径转交并等 `ack`
  父（编排者）  : 依次起 A / B，汇总三份 JSON

用法（工作目录 `jflove-desktop`）：
    .\\venv-win\\Scripts\\python.exe poc\\editor-probes\\r3-single-instance-roundtrip.py

产物：`out/r3-primary.json` / `out/r3-secondary.json` / `out/r3-roundtrip.json`（+ `.log`）
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
#: 模块根（`jflove-desktop/`）：子进程要以脚本方式 import `src.*`，脚本目录不在 sys.path
ROOT = HERE.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = HERE / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)
NAME = "jflove-r3-roundtrip"
TARGET = r"D:\资料\往返 测试.txt"


def _run_primary(name: str, out_file: str) -> int:
    """首实例：listen + 注入 sink + 等路径到达"""
    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

    from src.utils.single_instance import SingleInstanceGuard

    app = QCoreApplication([])
    guard = SingleInstanceGuard(name)
    is_primary = bool(guard.try_become_primary())
    received: list = []
    guard.set_paths_sink(lambda paths, source: received.append([paths, source]))
    Path(out_file).write_text(
        json.dumps({"role": "primary", "is_primary": is_primary}, ensure_ascii=False),
        encoding="utf-8",
    )
    deadline = time.monotonic() + 6.0
    while not received and time.monotonic() < deadline:
        loop = QEventLoop()
        QTimer.singleShot(100, loop.quit)
        loop.exec()
    guard.close()
    Path(out_file).write_text(
        json.dumps({"role": "primary", "is_primary": is_primary, "received": received},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


def _run_secondary(name: str, path: str, out_file: str) -> int:
    """第二实例：判定应为非首实例，然后转交路径"""
    from PySide6.QtCore import QCoreApplication

    from src.utils.single_instance import SingleInstanceGuard

    app = QCoreApplication([])  # noqa: F841
    guard = SingleInstanceGuard(name)
    is_primary = bool(guard.try_become_primary())
    forwarded = bool(guard.forward([path], cwd="."))
    Path(out_file).write_text(
        json.dumps({"role": "secondary", "is_primary": is_primary, "forwarded": forwarded},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    return 0


def main() -> int:
    if len(sys.argv) > 3 and sys.argv[1] == "--primary":
        return _run_primary(sys.argv[2], sys.argv[3])
    if len(sys.argv) > 4 and sys.argv[1] == "--secondary":
        return _run_secondary(sys.argv[2], sys.argv[3], sys.argv[4])

    script = str(Path(__file__).resolve())
    exe = sys.executable
    primary_file = str(OUT_DIR / "r3-primary.json")
    secondary_file = str(OUT_DIR / "r3-secondary.json")
    for stale in (primary_file, secondary_file):
        Path(stale).unlink(missing_ok=True)

    log: list[str] = []

    def say(line: str) -> None:
        log.append(line)
        print(line, flush=True)

    proc_a = subprocess.Popen([exe, script, "--primary", NAME, primary_file],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # 等 A 就绪（它写完 {role:primary,is_primary:…} 才算 listen 完成）
    ready_deadline = time.monotonic() + 8.0
    primary_first = None
    while time.monotonic() < ready_deadline:
        if Path(primary_file).exists():
            primary_first = json.loads(Path(primary_file).read_text(encoding="utf-8"))
            if "is_primary" in primary_first:
                break
        time.sleep(0.05)
    say(f"R3 A 就绪: {primary_first}")

    proc_b = subprocess.run([exe, script, "--secondary", NAME, TARGET, secondary_file],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    secondary = (json.loads(Path(secondary_file).read_text(encoding="utf-8"))
                 if Path(secondary_file).exists() else None)
    say(f"R3 B 结果: {secondary}")
    if secondary is None:
        say(f"R3 B stderr: {(proc_b.stderr or '')[-400:]}")

    # 等 A 收到并落最终结果
    final_deadline = time.monotonic() + 8.0
    primary_final = None
    while time.monotonic() < final_deadline:
        data = json.loads(Path(primary_file).read_text(encoding="utf-8"))
        if "received" in data:
            primary_final = data
            break
        time.sleep(0.1)
    proc_a.wait(timeout=10)
    say(f"R3 A 结果: {primary_final}")

    received = (primary_final or {}).get("received") or []
    report = {
        "name": NAME,
        "target": TARGET,
        "A_is_primary": (primary_first or {}).get("is_primary"),
        "A_received": received,
        "B_is_primary": (secondary or {}).get("is_primary"),
        "B_forwarded": (secondary or {}).get("forwarded"),
        "B_exit_code": proc_b.returncode,
        "A_exit_code": proc_a.returncode,
        "verdict": {
            "首实例判定正确": (primary_first or {}).get("is_primary") is True,
            "第二实例不自认首实例": (secondary or {}).get("is_primary") is False,
            "转交获确认": (secondary or {}).get("forwarded") is True,
            # `sink(paths, source)` ⇒ 元素形如 [[paths…], source]
            "首实例收到同一路径": received[:1] == [[[TARGET], "single_instance"]],
        },
    }
    report["all_passed"] = all(report["verdict"].values())
    say(f"R3 判定: {json.dumps(report['verdict'], ensure_ascii=False)}；all_passed={report['all_passed']}")

    (OUT_DIR / "r3-roundtrip.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT_DIR / "r3-roundtrip.log").write_text("\n".join(log) + "\n", encoding="utf-8")
    say(f"R3 证据已写入 {OUT_DIR}")
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
