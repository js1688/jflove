#!/usr/bin/env python3
"""
F5 真机负向对照执行器（r16 单测锚点的**真机**补充；v1.6.1 用户反馈 F5 / AC-77①）

## 为什么需要它（而不是直接塞进 r16）

`r16-negative-matrix.py` 的机制是「改 `ui/src/**` → **直接**跑判据」。这对 vitest / pytest 成立，
但对**真机判据**不成立：`r15` 的 AC68-2 跑的是外壳加载的**构建产物**（`ui/dist` 的 bundle），
改源码不改 bundle ⇒ 探针测到的还是旧代码（假绿）。所以真机负向对照必须多一步 `vite build`：

    apply（改源码） → build（把当前源码构建到证据目录） → probe（r15 --only 68 --webui 新产物）
    → restore（**按字节还原** + sha256 复验 + git status 逐行比对）

本执行器把这三步 + 还原串成一条命令，还原放在 `finally` 里 ⇒ **不会漏还原**。

## 变异（与 `r16-negative-matrix.py::F5-SNAPSHOT-NOOP` **同一锚点、同一语义**）

`ui/src/stores/scratch-store.ts::snapshotInitialText` 的正文写回变成 no-op
（= 修复前"宿主卸载时什么都不存"），其余一字不动。

## 用法

    venv-win\\Scripts\\python.exe poc\\editor-probes\\r16-f5-real-gui-control.py \\
        --out poc\\editor-probes\\out\\r15-<证据目录>          # 构建产物落 <out>/.tmp_webui/webui

（无子命令：一条命令即"施加变异 → 构建 → 跑真机判据 → 还原"。）

退出码：0 = 变异后 r15 的 AC68-2 **确实 FAIL** 且**字节还原成功**；1 = 没红；2 = 脚本/环境错误。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve()
DESKTOP = SCRIPT.parents[2]
REPO = SCRIPT.parents[3]

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

NODE = shutil.which("node") or "node"
PY = str(DESKTOP / "venv-win" / "Scripts" / "python.exe")

TARGET_REL = "jflove-desktop/ui/src/stores/scratch-store.ts"
TARGET = REPO / TARGET_REL

#: 与 r16 的 `F5-SNAPSHOT-NOOP` 逐字相同的锚点与替换（**同一变异**，便于两处互证）
OLD = (
    "  const tab = useScratchStore.getState().tabs.find((item) => item.id === tabId);\n"
    "  if (!tab) return; // ① 守卫：标签已关闭 / 从不存在 ⇒ 不写入（防\"死标签复活\"）\n"
    "  initialTexts.set(tabId, text);\n"
)
NEW = (
    "  // MUTATION：F5 负向注入 —— 卸载前的快照写回变成 no-op（等价于修复前\"什么都不存\"）\n"
    "  void tabId;\n"
    "  void text;\n"
    "  void meta;\n"
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_status_line() -> str:
    """目标文件的 `git status --porcelain` 行（还原前后必须逐字相同）。"""
    proc = subprocess.run(
        ["git", "status", "--porcelain", "--", TARGET_REL],
        cwd=str(REPO), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.stdout.strip()


class Control:
    def __init__(self, out_dir: Path, webui_dir: Path) -> None:
        self.out = out_dir
        self.webui = webui_dir
        self.backup = out_dir / ".tmp_f5neg_original.bin"
        self.evidence: dict = {
            "probe": "r16-f5-real-gui-control",
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
        self.backup.write_bytes(data)  # 备份**原始字节**（也是还原的唯一依据）
        mutated = text.replace(old, new, 1).encode("utf-8")
        TARGET.write_bytes(mutated)
        info = {
            "sha256Before": sha256_bytes(data),
            "sha256Mutated": sha256_bytes(TARGET.read_bytes()),
            "backup": str(self.backup),
            "backupSha256": sha256_bytes(self.backup.read_bytes()),
            "anchorHits": hits,
            "sizeBefore": len(data),
            "sizeMutated": len(mutated),
            "gitStatusBefore": git_status_line(),
        }
        self.evidence["apply"] = info
        print(f"[apply] sha256 前={info['sha256Before'][:16]} 后={info['sha256Mutated'][:16]} "
              f"锚点命中={hits} 备份={self.backup.name}")
        return info

    # ── ② build（**在变异生效期间**把 ui/src 构建到证据目录）──
    def build(self, log_name: str = "vite-build-mutated.log") -> dict:
        started = time.monotonic()
        self.webui.parent.mkdir(parents=True, exist_ok=True)
        argv = [NODE, "node_modules/vite/bin/vite.js", "build",
                "--outDir", str(self.webui), "--emptyOutDir"]
        proc = subprocess.run(
            argv, cwd=str(DESKTOP / "ui"), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        (self.out / log_name).write_text(out, encoding="utf-8")
        index = self.webui / "index.html"
        info = {
            "argv": argv, "exit": proc.returncode, "costSec": round(time.monotonic() - started, 1),
            "log": str(self.out / log_name), "webui": str(self.webui),
            "indexExists": index.exists(),
            "indexSha256": sha256_bytes(index.read_bytes()) if index.exists() else None,
            "tail": "\n".join([ln for ln in out.splitlines() if ln.strip()][-6:]),
        }
        self.evidence["build"] = info
        print(f"[build] exit={info['exit']} 用时 {info['costSec']}s "
              f"index={info['indexExists']} sha={str(info['indexSha256'])[:16]}")
        if proc.returncode != 0:
            raise RuntimeError(f"vite build 失败（exit {proc.returncode}），见 {info['log']}")
        return info

    # ── ③ probe（真机判据）──
    def probe(self, only: str, tag: str) -> dict:
        started = time.monotonic()
        probe_out = self.out / tag
        argv = [PY, "poc/editor-probes/r15-real-gui-ac64-ac71.py", "--only", only,
                "--webui", str(self.webui), "--out", str(probe_out)]
        proc = subprocess.run(
            argv, cwd=str(DESKTOP), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=1200,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        (self.out / f"r15-{tag}.log").write_text(out, encoding="utf-8")
        ac68 = [ln for ln in out.splitlines() if "AC68-2" in ln]
        info = {
            "argv": argv, "exit": proc.returncode, "costSec": round(time.monotonic() - started, 1),
            "log": str(self.out / f"r15-{tag}.log"), "probeOut": str(probe_out),
            "ac68Lines": ac68[:3],
            "ac68Failed": any(ln.strip().startswith("[FAIL] AC68-2") for ln in ac68),
            "tail": "\n".join([ln for ln in out.splitlines() if ln.strip()][-8:]),
        }
        self.evidence["probe"] = info
        print(f"[probe] exit={info['exit']} 用时 {info['costSec']}s "
              f"AC68-2 FAIL={info['ac68Failed']}")
        for line in info["tail"].splitlines():
            print(f"        {line}")
        return info

    # ── ④ restore（**按字节还原** + sha256 复验 + git status 逐行比对）──
    def restore(self) -> dict:
        original = self.backup.read_bytes()
        TARGET.write_bytes(original)
        now = TARGET.read_bytes()
        info = {
            "sha256Restored": sha256_bytes(now),
            "sha256Backup": sha256_bytes(original),
            "bytesEqual": now == original,
            "shaEqual": sha256_bytes(now) == sha256_bytes(original),
            "gitStatusAfter": git_status_line(),
            "gitStatusUnchanged": git_status_line()
            == (self.evidence.get("apply") or {}).get("gitStatusBefore"),
        }
        info["ok"] = bool(info["bytesEqual"] and info["shaEqual"]
                          and info["gitStatusUnchanged"])
        self.evidence["restore"] = info
        print(f"[restore] sha={info['sha256Restored'][:16]} 字节一致={info['bytesEqual']} "
              f"git 状态一致={info['gitStatusUnchanged']}")
        return info

    def write(self) -> Path:
        path = self.out / "r16-f5neg-control.json"
        path.write_text(json.dumps(self.evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def main() -> int:
    parser = argparse.ArgumentParser(description="F5 真机负向对照执行器（apply → build → probe → restore）")
    parser.add_argument("--out", required=True, help="证据目录")
    parser.add_argument("--only", default="68", help="r15 的判据（默认 68 = AC-68 ①②）")
    parser.add_argument("--tag", default="f5neg-mutated", help="探针证据子目录名")
    parser.add_argument("--webui", default="", help="构建产物的 webui 目录（默认 <out>/.tmp_webui/webui）")
    args = parser.parse_args()

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = Path.cwd() / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    webui_dir = Path(args.webui) if args.webui else (out_dir / ".tmp_webui" / "webui")
    if not webui_dir.is_absolute():
        webui_dir = Path.cwd() / webui_dir

    control = Control(out_dir, webui_dir)
    print(f"F5 真机负向对照：目标={TARGET_REL}")
    print(f"证据目录={out_dir}")
    print(f"构建产物={webui_dir}")
    try:
        control.apply()
        control.build()
        probe = control.probe(args.only, args.tag)
    finally:
        restore = control.restore()
        path = control.write()
        print(f"证据 JSON：{path}")

    if not restore.get("ok"):
        print("[错误] 还原未通过 sha256 / git 状态复验 —— 必须人工处理")
        return 2
    if probe.get("exit") == 0 or not probe.get("ac68Failed"):
        print("[未抓] 变异后 AC68-2 仍然通过（或另有原因退出）⇒ 该判据在空转")
        return 1
    print("[OK] 变异后 AC68-2 按预期 FAIL，且源码已按字节还原")
    return 0


if __name__ == "__main__":
    sys.exit(main())
