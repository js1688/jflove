#!/usr/bin/env python3
"""
F6 真机负向对照执行器（r16 单测锚点的**真机**补充；v1.6.1 用户反馈 F6 / BL-51）

## 它做什么

真机判据是 `r17 --only CLIP-AWT-REAL`（需剪贴板上**已由 JDK 写好真实 AWT 载荷**且 JVM 驻留）。
本执行器把整条对照串成一条命令，并保证**按字节还原**：

    apply（关掉外壳侧归一化开关） → java（AwtClipHtml 把真实 AWT 载荷放上剪贴板并驻留）
    → probe（r17 --only CLIP-AWT-REAL，必须 FAIL） → restore（按字节还原 + sha256 复验） → kill java

变异（与 `r16-negative-matrix.py::F6-CLIPBOARD-NORMALIZE-OFF` **同一锚点、同一语义**）：
`src/ui/web_shell.py` 的 `CLIPBOARD_NORMALIZE_ENABLED = sys.platform == "win32"` → `False`
（= 修复前"根本不做归一化"）。

## 为什么没有把它塞进 r16

r16 的判据只看 `argv` 的退出码；而 r17 在"剪贴板前置未满足"时**也会** FAIL
（`why=clipboard-has-no-text-and-no-html`）⇒ 无法区分"因变异而红"和"因前置缺失而红"（假绿）。
本执行器额外解析 `r17-result.json`：要求 `equalAnyAttempt=false` **且** 按键送达自检
`osKeyDeliveryCheck.ok=true`（排除"探针按键没送到"这种非结论性红）。

## 用法

    venv-win\\Scripts\\python.exe poc\\editor-probes\\r16-f6-real-gui-control.py \\
        --out poc\\editor-probes\\out\\r17-<证据目录>

退出码：0 = 变异后 CLIP-AWT-REAL **按预期 FAIL（且是"粘贴无反应"这一种）** + 字节还原成功；
1 = 没红；2 = 脚本/环境错误（含 java 起不来、还原复验失败）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve()
DESKTOP = SCRIPT.parents[2]
REPO = SCRIPT.parents[3]

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

PY = str(DESKTOP / "venv-win" / "Scripts" / "python.exe")
DEFAULT_JAVA = r"D:\jdk\jdk-21.0.9\bin\java.exe"
JAVA_SRC_REL = "poc/editor-probes/AwtClipHtml.java"

TARGET_REL = "jflove-desktop/src/ui/web_shell.py"
TARGET = REPO / TARGET_REL

#: 与 r16 的 `F6-CLIPBOARD-NORMALIZE-OFF` 逐字相同的锚点与替换（**同一变异**）
OLD = 'CLIPBOARD_NORMALIZE_ENABLED = sys.platform == "win32"\n'
NEW = (
    "# MUTATION：F6 负向注入 —— 关掉平台开关（等价于修复前\"不做归一化\"）\n"
    "CLIPBOARD_NORMALIZE_ENABLED = False\n"
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_status_line() -> str:
    proc = subprocess.run(
        ["git", "status", "--porcelain", "--", TARGET_REL],
        cwd=str(REPO), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.stdout.strip()


class Control:
    def __init__(self, out_dir: Path, java: str) -> None:
        self.out = out_dir
        self.java = java
        self.backup = out_dir / ".tmp_f6neg_original.bin"
        self.java_log = out_dir / "awt-clipboard.log"
        self.java_proc: subprocess.Popen | None = None
        self.evidence: dict = {
            "probe": "r16-f6-real-gui-control",
            "target": TARGET_REL,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    # ── ① apply ──
    def apply(self) -> dict:
        data = TARGET.read_bytes()
        text = data.decode("utf-8")
        old, new = OLD, NEW
        if text.count(old) != 1 and "\r\n" in text:
            old = old.replace("\n", "\r\n")
            new = new.replace("\n", "\r\n")
        hits = text.count(old)
        if hits != 1:
            raise RuntimeError(f"变异锚点命中 {hits} 次（要求恰好 1 次）⇒ 拒绝变异")
        self.backup.parent.mkdir(parents=True, exist_ok=True)
        self.backup.write_bytes(data)
        TARGET.write_bytes(text.replace(old, new, 1).encode("utf-8"))
        info = {
            "sha256Before": sha256_bytes(data),
            "sha256Mutated": sha256_bytes(TARGET.read_bytes()),
            "anchorHits": hits,
            "gitStatusBefore": git_status_line(),
        }
        self.evidence["apply"] = info
        print(f"[apply] sha256 前={info['sha256Before'][:16]} 后={info['sha256Mutated'][:16]} "
              f"锚点命中={hits}")
        return info

    # ── ② java：把真实 AWT 载荷放上剪贴板并驻留 ──
    def start_java(self, flavor: str, preset: str, hold: int) -> dict:
        if not Path(self.java).exists():
            raise RuntimeError(f"未找到 java：{self.java}")
        java_src = DESKTOP / JAVA_SRC_REL
        if not java_src.exists():
            raise RuntimeError(f"未找到 AwtClipHtml.java：{java_src}")
        argv = [self.java, str(java_src), f"--flavor={flavor}", f"--preset={preset}",
                f"--hold={hold}"]
        log = self.java_log.open("wb")
        self.java_proc = subprocess.Popen(  # noqa: S603 - 解释器与参数均为本仓库内固定值
            argv, cwd=str(DESKTOP), stdout=log, stderr=subprocess.STDOUT)
        started = time.monotonic()
        heartbeat = False
        while time.monotonic() - started < 45:
            time.sleep(2)
            if self.java_proc.poll() is not None:
                break
            if self.java_log.exists() and "[hb]" in self.java_log.read_text(
                    encoding="utf-8", errors="replace"):
                heartbeat = True
                break
        info = {
            "argv": argv, "pid": self.java_proc.pid, "log": str(self.java_log),
            "heartbeatSeen": heartbeat, "alive": self.java_proc.poll() is None,
            "waitSec": round(time.monotonic() - started, 1),
        }
        self.evidence["java"] = info
        print(f"[java] pid={info['pid']} 心跳={heartbeat} 存活={info['alive']} "
              f"等待 {info['waitSec']}s")
        if not info["alive"]:
            tail = self.java_log.read_text(encoding="utf-8", errors="replace")[-600:]
            raise RuntimeError(f"java 未能驻留（载荷可能已失效）：{tail}")
        return info

    # ── ③ probe ──
    def probe(self, only: str, tag: str) -> dict:
        probe_out = self.out / tag
        argv = [PY, "poc/editor-probes/r17-clipboard-cfhtml.py", "--only", only,
                "--out", str(probe_out)]
        started = time.monotonic()
        proc = subprocess.run(
            argv, cwd=str(DESKTOP), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=1500)
        out = (proc.stdout or "") + (proc.stderr or "")
        (self.out / f"r17-{tag}.log").write_text(out, encoding="utf-8")
        lines = [ln for ln in out.splitlines() if "CLIP-AWT-REAL" in ln]
        detail: dict = {}
        result_json = probe_out / "r17-result.json"
        if result_json.exists():
            payload = json.loads(result_json.read_text(encoding="utf-8"))
            for check in payload.get("checks", []):
                if check.get("id") == "CLIP-AWT-REAL":
                    d = check.get("detail") or {}
                    detail = {
                        "status": check.get("status"),
                        "verdictNote": d.get("verdictNote"),
                        "equalAnyAttempt": d.get("equalAnyAttempt"),
                        "attemptCount": d.get("attemptCount"),
                        "doesQtSeeHtml": d.get("doesQtSeeHtml"),
                        "clipboardUntouchedByProbe": d.get("clipboardUntouchedByProbe"),
                        "osKeyDeliveryOk": (d.get("osKeyDeliveryCheck") or {}).get("ok"),
                        "docCodePointsAfter": (d.get("after") or {}).get("docCodePoints"),
                        "expectationSource": (d.get("expectation") or {}).get("source"),
                        "win32Formats": (d.get("win32ClipboardAtPasteTime") or {}).get("formats"),
                    }
        info = {
            "argv": argv, "exit": proc.returncode, "costSec": round(time.monotonic() - started, 1),
            "log": str(self.out / f"r17-{tag}.log"),
            "clipLines": lines[:2], "failedLine": any(
                ln.strip().startswith("[FAIL] CLIP-AWT-REAL") for ln in lines),
            "detail": detail,
            "tail": "\n".join([ln for ln in out.splitlines() if ln.strip()][-8:]),
        }
        self.evidence["probe"] = info
        print(f"[probe] exit={info['exit']} 用时 {info['costSec']}s "
              f"CLIP-AWT-REAL FAIL={info['failedLine']}")
        print(f"        detail={json.dumps(detail, ensure_ascii=False)}")
        return info

    # ── ④ restore ──
    def restore(self) -> dict:
        original = self.backup.read_bytes()
        TARGET.write_bytes(original)
        now = TARGET.read_bytes()
        info = {
            "sha256Restored": sha256_bytes(now),
            "sha256Backup": sha256_bytes(original),
            "bytesEqual": now == original,
            "gitStatusAfter": git_status_line(),
            "gitStatusUnchanged": git_status_line()
            == (self.evidence.get("apply") or {}).get("gitStatusBefore"),
        }
        info["ok"] = bool(info["bytesEqual"] and info["gitStatusUnchanged"])
        self.evidence["restore"] = info
        print(f"[restore] sha={info['sha256Restored'][:16]} 字节一致={info['bytesEqual']} "
              f"git 状态一致={info['gitStatusUnchanged']}")
        return info

    def stop_java(self) -> dict:
        info: dict = {"wasAlive": False}
        if self.java_proc is not None:
            info["wasAlive"] = self.java_proc.poll() is None
            if info["wasAlive"]:
                self.java_proc.terminate()
                try:
                    self.java_proc.wait(timeout=10)
                    info["terminated"] = True
                except Exception:  # noqa: BLE001 - 收尾尽力而为
                    self.java_proc.kill()
                    info["killed"] = True
        self.evidence["javaStop"] = info
        print(f"[java] 收尾：{json.dumps(info, ensure_ascii=False)}")
        return info

    def write(self) -> Path:
        path = self.out / "r16-f6neg-control.json"
        path.write_text(json.dumps(self.evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def main() -> int:
    parser = argparse.ArgumentParser(description="F6 真机负向对照（apply → java → r17 → restore）")
    parser.add_argument("--out", required=True, help="证据目录")
    parser.add_argument("--only", default="CLIP-AWT-REAL", help="r17 的判据（默认 CLIP-AWT-REAL）")
    parser.add_argument("--tag", default="f6neg-mutated", help="探针证据子目录名")
    parser.add_argument("--java", default=DEFAULT_JAVA, help="java.exe 路径")
    parser.add_argument("--flavor", default="all", help="AwtClipHtml 的 html flavor 形态")
    parser.add_argument("--preset", default="ascii", help="AwtClipHtml 的预设载荷")
    parser.add_argument("--hold", type=int, default=900, help="JVM 驻留秒数")
    args = parser.parse_args()

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = Path.cwd() / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    control = Control(out_dir, args.java)
    print(f"F6 真机负向对照：目标={TARGET_REL}")
    print(f"证据目录={out_dir}")
    probe: dict = {}
    try:
        control.apply()
        control.start_java(args.flavor, args.preset, args.hold)
        probe = control.probe(args.only, args.tag)
    finally:
        restore = control.restore()
        control.stop_java()
        path = control.write()
        print(f"证据 JSON：{path}")

    if not restore.get("ok"):
        print("[错误] 还原未通过字节 / git 状态复验 —— 必须人工处理")
        return 2
    detail = probe.get("detail") or {}
    conclusive = bool(detail.get("osKeyDeliveryOk") is True
                      and detail.get("equalAnyAttempt") is False)
    if probe.get("failedLine") and conclusive:
        print("[OK] 变异后 CLIP-AWT-REAL 按预期 FAIL（粘贴 0 字符，且按键送达自检通过），"
              "源码已按字节还原")
        return 0
    print("[未抓] 变异后 CLIP-AWT-REAL 没有按预期红，或红得**不具结论性**"
          f"（osKeyDeliveryOk={detail.get('osKeyDeliveryOk')}，"
          f"equalAnyAttempt={detail.get('equalAnyAttempt')}）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
