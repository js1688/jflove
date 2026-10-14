"""
r10 强杀-重启复核探针（Phase 6 · P6-5）

## 这个探针解决什么

设计 §14.3 里两条 AC **只能在真机（真进程被强杀）上取证**：

- **AC-S2**：≥5 次强杀 → 索引可加载、**最多丢最后一个窗口**、**无半截旧草稿**。
- **AC-58**：托盘后草稿未清、恢复后仍在；**强杀后重启仍还原**（走流程 C）。

## 怎么取证（两条互补的路径）

1. **父进程 + 写草稿子进程（AC-S2）**：子进程以 ~60 ms 间隔反复
   `scratch_service.save_draft(pane, marker_j)`（真服务层 API，原子写落 `.tmp/` →
   `os.replace`），父进程在**随机时刻** `taskkill /F /T` 把它打断，然后**在新进程里**
   读磁盘做判定：草稿内容必须**完整等于某个 marker**（不得半截）、且落在
   「最后一次尝试」或「倒数第二次尝试」上（最多丢最后一个窗口）、`clean_exit == false`
   （崩溃检测成立 ⇒ 重启应触发还原）、`.tmp/` 里残留的索引副本必须仍是**合法 JSON**。
2. **真外壳 + 真托盘（AC-58）**：子进程起**真外壳**（`Bridge` + `WebShell` + `load_ui()`，
   与 `src/main.py` 同接线），经**生产写路径**（`HANDLERS['editor']['draftSave']`）落一份草稿，
   再 `hide_to_tray()` 收进托盘 —— 判定「托盘后草稿未清」；随后父进程强杀它，
   判定「强杀后重启仍能读到同一份完整草稿」（`list_drafts()` + `is_abnormal_exit()`）。

## 与 r9/r11 的分工

- r9（变异矩阵）证明的是**判据有牙**：把源码改坏，对应用例必须红。
- r11（真机驱动）证明的是**渲染层接线**（OS 级按键 / 滚轮 / 真几何 / 零远端）。
- r10（本探针）证明的是**崩溃边界**：真进程被强杀时草稿库的一致性。

## 沙箱（不碰用户真实目录）

`APPDATA` / `LOCALAPPDATA` 指向本次 `out/<run>/appdata`（必须在导入
`src.config.settings` **之前**设置），并把 `settings.SCRATCH_DIR` 逐轮指向
`out/<run>/scratch-*`；子进程继承同一环境变量并被显式告知草稿目录。
**绝不触碰真实用户目录与仓库内其它文件。**

## 怎么跑

    jflove-desktop\\venv-win\\Scripts\\python.exe poc\\editor-probes\\r10-kill-restart.py
    ... --rounds 6            # AC-S2 的强杀轮数（默认 6，下限 5）
    ... --no-gui              # 只跑 AC-S2 半边（无显示环境用）

## 退出码

- `0` 必须通过的判据全过（SKIP 不算失败、不阻塞退出码）
- `1` 有必须通过的判据失败
- `2` 环境/脚本错误（QtWebEngine 起不来、子进程起不来）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT = Path(__file__).resolve()
DESKTOP = SCRIPT.parents[2]
REPO = SCRIPT.parents[3]

# Windows 控制台是 GBK：日志里有 ⇒/· 等字符，不重配置会 UnicodeEncodeError 中断探针
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

sys.path.insert(0, str(DESKTOP))

PANE_FILE_PREFIX = "pane-"
PANE_FILE_SUFFIX = ".txt"
INDEX_NAME = "index.json"
TMP_DIR_NAME = ".tmp"


# ─────────────────────────── 判定与留证 ───────────────────────────


class Report:
    """收集每条判据的结果，最后写 JSON + 决定退出码。"""

    def __init__(self, out_dir: Path) -> None:
        self.out = out_dir
        self.checks: list[dict] = []
        self.notes: list[str] = []

    def say(self, message: str) -> None:
        print(message, flush=True)

    def note(self, message: str) -> None:
        self.notes.append(message)
        self.say(f"  · {message}")

    def add(self, cid: str, desc: str, status: str, detail, gating: bool = True) -> None:
        """status: PASS / FAIL / SKIP（SKIP 同样不阻塞退出码）。"""
        record = {"id": cid, "desc": desc, "status": status, "gating": gating, "detail": detail}
        self.checks.append(record)
        mark = {"PASS": "[ OK ]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}[status]
        suffix = "" if gating else " （观察项，不参与退出码）"
        self.say(f"{mark} {cid} {desc}{suffix}")
        if detail not in (None, {}, []):
            self.say(f"       {json.dumps(detail, ensure_ascii=False)[:600]}")

    def verdict(self) -> int:
        failed = [c for c in self.checks if c["gating"] and c["status"] == "FAIL"]
        return 1 if failed else 0

    def write(self, extra: dict) -> Path:
        payload = {
            "probe": "r10-kill-restart",
            "at": datetime.now().isoformat(timespec="seconds"),
            "checks": self.checks,
            "notes": self.notes,
            **extra,
        }
        path = self.out / "r10-result.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


# ─────────────────────────── 子进程模式 ───────────────────────────


def _pane_meta(pane_id: str, fixture: Path | None, *, draft_form: bool) -> dict:
    """构造一份与渲染层同形的窗格元数据（§7.1 #6）。"""
    title = fixture.name if fixture is not None else "r10-draft.txt"
    return {
        "id": pane_id,
        "title": title,
        "path": None if draft_form else str(fixture),
        "language": "text",
        "encoding": "utf-8",
        "lineEnding": "lf",
        "readOnly": False,
        "dirty": True,
        "viewMode": "edit",
        "mounted": True,
        "geometry": {"x": 40, "y": 40, "w": 900, "h": 600},
        "caret": {"line": 1, "col": 1},
    }


def _marker(tag: str, index: int, width: int) -> str:
    return f"ROUND-{tag}-{index:03d}-" + "x" * width


def child_drafts(args: argparse.Namespace) -> int:
    """子进程：以固定间隔连续写草稿，直到被父进程强杀。"""
    from src.config import settings

    settings.SCRATCH_DIR = args.scratch
    from src.services import scratch_service

    pane = _pane_meta(args.pane_id, None, draft_form=True)
    log = Path(args.log)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as handle:
        index = 0
        while True:
            # 先记账再写盘：父进程据此知道「最后一次尝试」是第几次，从而判定丢了几个窗口
            handle.write(f"{index}\n")
            handle.flush()
            os.fsync(handle.fileno())
            scratch_service.save_draft(pane, _marker(args.tag, index, args.marker_len))
            index += 1
            # 间隔带抖动：让强杀点相对「写内容」与「写索引」两个窗口漂移
            time.sleep((args.gap_ms + (index % 3) * 7) / 1000.0)


def child_gui(args: argparse.Namespace) -> int:
    """子进程：真外壳 + 真托盘；经生产写路径落一份草稿，收进托盘后待杀。"""
    state_path = Path(args.state)
    result: dict = {"ok": False, "stage": "start"}

    def flush_state(**extra) -> None:
        result.update(extra)
        state_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        from src.config import settings

        settings.SCRATCH_DIR = args.scratch
        if args.no_window:
            os.environ["QT_QPA_PLATFORM"] = "offscreen"

        from PySide6.QtCore import QTimer
        from src.bridge import Bridge
        from src.bridge import editor as editor_bridge
        from src.ui.web_shell import WebShell, build_app

        app = build_app()
        app.setQuitOnLastWindowClosed(False)
        bridge = Bridge()
        shell = WebShell(objects={"bridge": bridge}, frameless=True,
                         publish_event=bridge.publish_event)
        editor_bridge.set_main_window(shell)
        shell.setup_tray(bridge.publish_event)
        flush_state(stage="built", tray=shell.tray is not None)

        result["load_ui"] = bool(shell.load_ui())
        flush_state(stage="loaded")
        if not result["load_ui"]:
            return 2
        shell.show()

        def step_draft() -> None:
            """经生产写路径（桥 handler）落草稿，随后收进托盘。"""
            try:
                from src.bridge.editor import HANDLERS

                pane = _pane_meta(args.pane_id, Path(args.fixture), draft_form=False)
                handler = HANDLERS["editor"]["draftSave"]
                reply = handler({"pane": pane, "content": args.marker})
                draft = Path(args.scratch) / f"{PANE_FILE_PREFIX}{args.pane_id}{PANE_FILE_SUFFIX}"
                result["draftReply"] = reply
                result["draftExists"] = draft.exists()
                result["draftContent"] = draft.read_text(encoding="utf-8") if draft.exists() else None
                visible_before = shell.isVisible()
                shell.hide_to_tray()
                result["visibleBefore"] = visible_before
                result["visibleAfter"] = shell.isVisible()
                result["trayObject"] = shell.tray is not None
                flush_state(stage="tray", ok=True)
            except Exception as exc:  # noqa: BLE001 - 子进程要把失败原因写回状态文件
                import traceback

                traceback.print_exc()
                flush_state(stage="tray-error", ok=False, error=str(exc))

        QTimer.singleShot(int(args.delay_ms), step_draft)
        QTimer.singleShot(int(args.delay_ms) + 180000, lambda: os._exit(4))  # 自保看门狗
        return int(app.exec())

    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        try:
            flush_state(stage="fatal", ok=False, error=str(exc))
        except Exception:  # noqa: BLE001 - 状态文件都写不下去就只能靠退出码
            pass
        return 2


# ─────────────────────────── 父进程判定辅助 ───────────────────────────


def read_marker_index(path: Path, tag: str, width: int) -> tuple[int | None, str]:
    """读取草稿文件并判断它是否是**完整** marker；返回 (轮次, 内容摘要)。"""
    if not path.exists():
        return None, "<缺失>"
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.fullmatch(rf"ROUND-{re.escape(tag)}-(\d{{3}})-x{{{width}}}", text)
    if match is None:
        return None, text[:120] + (f"…(len={len(text)})" if len(text) > 120 else "")
    return int(match.group(1)), f"完整 marker（{len(text)} 字节）"


def tail_attempt(path: Path) -> int | None:
    """读子进程的尝试日志，取最后一次「已记账」的轮次。"""
    if not path.exists():
        return None
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return int(lines[-1]) if lines else None


def kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                   capture_output=True, text=True, check=False)


def tmp_residue(scratch: Path) -> dict:
    """`.tmp/` 残留：数量 + 索引副本是否仍是合法 JSON（AC-S2 ① 半截 JSON 容错）。"""
    tmp_dir = scratch / TMP_DIR_NAME
    files = sorted(tmp_dir.glob("*.tmp")) if tmp_dir.is_dir() else []
    broken: list[str] = []
    for item in files:
        if not item.name.startswith(INDEX_NAME):
            continue
        try:
            json.loads(item.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 半截索引副本要能被点名
            broken.append(item.name)
    return {"count": len(files), "names": [f.name for f in files][:6], "brokenIndexCopies": broken}


def pane_payload(scratch: Path, pane_id: str) -> str | None:
    item = scratch / f"{PANE_FILE_PREFIX}{pane_id}{PANE_FILE_SUFFIX}"
    return item.read_text(encoding="utf-8") if item.exists() else None


# ─────────────────────────── 阶段一：AC-S2 强杀 ───────────────────────────


def phase_kill_rounds(report: Report, out_dir: Path, rounds: int, marker_len: int) -> None:
    """反复「写草稿子进程 + 随机时刻强杀」，逐轮读盘判定。"""
    from src.config import settings
    from src.services import scratch_service

    per_round: list[dict] = []
    for round_index in range(1, rounds + 1):
        scratch = out_dir / f"scratch-s2-r{round_index}"
        scratch.mkdir(parents=True, exist_ok=True)
        settings.SCRATCH_DIR = str(scratch)
        tag = f"r{round_index}"
        pane_id = f"r10kill{round_index:02d}"
        log = out_dir / f"attempts-r{round_index}.txt"
        argv = [
            sys.executable, str(SCRIPT), "--child-drafts",
            "--scratch", str(scratch), "--log", str(log), "--tag", tag,
            "--pane-id", pane_id, "--marker-len", str(marker_len), "--gap-ms", "60",
        ]
        child = subprocess.Popen(argv, cwd=str(DESKTOP), stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        # 强杀时刻逐轮漂移：让 kill 落在「写内容」「写索引」「sleep」三个窗口的不同位置
        time.sleep(0.35 + 0.11 * round_index)
        kill_tree(child.pid)
        child.wait(timeout=30)

        attempted = tail_attempt(log)
        draft = scratch / f"{PANE_FILE_PREFIX}{pane_id}{PANE_FILE_SUFFIX}"
        written, digest = read_marker_index(draft, tag, marker_len)
        try:
            listed = scratch_service.list_drafts()
        except Exception as exc:  # noqa: BLE001 - 必须永不抛异常（AC-S2 ①）
            listed = {"error": f"{type(exc).__name__}: {exc}"}
        entries = listed.get("entries") if isinstance(listed, dict) else None
        item = {
            "round": round_index,
            "attempted": attempted,
            "writtenIndex": written,
            "written": digest,
            "lostWindows": None if (attempted is None or written is None) else attempted - written,
            "cleanExit": listed.get("cleanExit") if isinstance(listed, dict) else None,
            "warnings": listed.get("warnings") if isinstance(listed, dict) else None,
            "entries": len(entries) if isinstance(entries, list) else None,
            "listedError": listed.get("error") if isinstance(listed, dict) else None,
            "abnormalExit": scratch_service.is_abnormal_exit(),
            "tmp": tmp_residue(scratch),
            "indexParses": _index_parses(scratch),
        }
        # 清理：清空草稿 → 干净退出标记（下一轮从未还原状态开始，判定可重复）
        try:
            scratch_service.clear_all_drafts()
        except Exception as exc:  # noqa: BLE001
            item["clearError"] = f"{type(exc).__name__}: {exc}"
        leftover = sorted(p.name for p in scratch.glob(f"{PANE_FILE_PREFIX}*{PANE_FILE_SUFFIX}"))
        item["leftoverPaneFiles"] = leftover
        item["staleTmpAfterClear"] = tmp_residue(scratch)["count"]
        scratch_service.mark_clean_exit()
        item["abnormalAfterClean"] = scratch_service.is_abnormal_exit()
        per_round.append(item)
        report.say(f"  · 第 {round_index} 轮：尝试={attempted} 落盘={written} "
                   f"（{digest}）cleanExit={item['cleanExit']}")

    def ok_all(pred) -> bool:
        return all(pred(item) for item in per_round)

    checks = {
        "① 索引可加载且不抛异常": ok_all(
            lambda i: i["listedError"] is None and i["entries"] == 1 and i["indexParses"]),
        "② 无半截草稿（内容=完整 marker）": ok_all(lambda i: i["writtenIndex"] is not None),
        "③ 最多丢最后一个窗口": ok_all(
            lambda i: i["lostWindows"] is not None and 0 <= i["lostWindows"] <= 1),
        "④ 崩溃检测成立（clean_exit=false）": ok_all(
            lambda i: i["cleanExit"] is False and i["abnormalExit"] is True),
        "⑤ .tmp 残留副本仍是合法 JSON": ok_all(lambda i: i["tmp"]["brokenIndexCopies"] == []),
        "⑥ 清空后无残留 + 干净标记生效": ok_all(
            lambda i: i["leftoverPaneFiles"] == [] and i["staleTmpAfterClear"] == 0
            and i["abnormalAfterClean"] is False),
    }
    detail = {"rounds": per_round, "checks": checks}
    status = "PASS" if all(checks.values()) else "FAIL"
    report.add(f"R-1 AC-S2：{rounds} 次强杀的可重复判定", "强杀后草稿完整、最多丢最后一个窗口",
               status, detail)
    if status == "FAIL":
        report.note("AC-S2 失败项：" + "、".join(k for k, v in checks.items() if not v))


def _index_parses(scratch: Path) -> bool:
    item = scratch / INDEX_NAME
    if not item.exists():
        return False
    try:
        payload = json.loads(item.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    return isinstance(payload, dict) and isinstance(payload.get("panes"), list)


# ─────────────────────────── 阶段一之补：半截状态注入（确定性） ───────────────────────────


def phase_half_state(report: Report, out_dir: Path, marker_len: int) -> None:
    """
    注入「写到一半就被强杀」的确定性残留，验证 AC-S2 ① 的两条容错路径。

    强杀轮次能不能正好落在 `os.replace` 的窗口里是概率问题（实测 8/8 都落在 sleep 窗口），
    所以这里把**中间状态直接种到磁盘上**，判据不再依赖运气：

    - (a) `.tmp/` 里留半截内容文件 + 半截索引副本：`list_drafts()` 必须仍返回**完整**草稿
      （草稿正文只从 `pane-<id>.txt` 读，`.tmp` 里的半截文件不得被当作草稿）。
    - (b) `index.json` 自身半截 + `.tmp/` 有更新的合法副本：必须从副本恢复，条目仍在、带 warning。
    - (c) `index.json` 直接消失：必须按草稿库目录重建条目（内容仍在），带 warning。
    """
    from src.config import settings
    from src.services import scratch_service

    scratch = out_dir / "scratch-half"
    scratch.mkdir(parents=True, exist_ok=True)
    settings.SCRATCH_DIR = str(scratch)
    pane_id = "r10half01"
    marker = _marker("half", 111, marker_len)
    scratch_service.save_draft(_pane_meta(pane_id, None, draft_form=True), marker)
    pane_file = scratch / f"{PANE_FILE_PREFIX}{pane_id}{PANE_FILE_SUFFIX}"
    tmp_dir = scratch / TMP_DIR_NAME
    index_file = scratch / INDEX_NAME

    def snapshot() -> dict:
        try:
            listed = scratch_service.list_drafts()
        except Exception as exc:  # noqa: BLE001 - 永不该发生，发生了要点名
            return {"error": f"{type(exc).__name__}: {exc}"}
        content = pane_file.read_text(encoding="utf-8") if pane_file.exists() else None
        return {"entries": len(listed.get("entries") or []), "cleanExit": listed.get("cleanExit"),
                "warnings": listed.get("warnings"), "contentComplete": content == marker,
                "contentLen": None if content is None else len(content)}

    # (a) 半截残留：内容写到一半 + 索引副本写到一半
    (tmp_dir / f"{PANE_FILE_PREFIX}{pane_id}{PANE_FILE_SUFFIX}.deadbeef.tmp").write_bytes(
        marker[: marker_len // 2].encode("utf-8"))
    (tmp_dir / f"{INDEX_NAME}.deadbeef.tmp").write_text('{"version": 1, "panes": [',
                                                        encoding="utf-8")
    snap_a = snapshot()
    ok_a = (snap_a.get("entries") == 1 and snap_a.get("contentComplete") is True
            and snap_a.get("cleanExit") is False and "error" not in snap_a)

    # (b) index.json 半截 + `.tmp/` 更新的合法副本
    raw = index_file.read_text(encoding="utf-8")
    (tmp_dir / f"{INDEX_NAME}.cafe.tmp").write_text(raw, encoding="utf-8")
    time.sleep(0.05)  # `_read_latest_tmp_index` 按 mtime 取最新，副本必须比坏文件新
    index_file.write_text(raw[: len(raw) // 2], encoding="utf-8")
    snap_b = snapshot()
    ok_b = (snap_b.get("entries") == 1 and snap_b.get("contentComplete") is True
            and bool(snap_b.get("warnings")) and "error" not in snap_b)

    # (c) index.json 消失 ⇒ 按目录重建
    index_file.unlink()
    snap_c = snapshot()
    ok_c = (snap_c.get("entries") == 1 and snap_c.get("contentComplete") is True
            and bool(snap_c.get("warnings")) and "error" not in snap_c)

    checks = {"(a) 半截 .tmp 残留不影响草稿完整性": ok_a,
              "(b) 索引半截 ⇒ 从 .tmp 最新副本恢复": ok_b,
              "(c) 索引消失 ⇒ 按草稿库目录重建": ok_c}
    report.add("R-3 AC-S2①：半截状态注入（确定性）",
               "半截索引/半截临时文件都能容错，草稿内容始终完整",
               "PASS" if all(checks.values()) else "FAIL",
               {"checks": checks, "a": snap_a, "b": snap_b, "c": snap_c})
    scratch_service.clear_all_drafts()
    scratch_service.mark_clean_exit()


# ─────────────────────────── 阶段二：AC-58 托盘 + 强杀 ───────────────────────────

def phase_tray_kill(report: Report, out_dir: Path, marker: str, no_gui: bool) -> None:
    """真外壳落草稿 → 收进托盘 → 强杀 → 新进程读盘判定可还原。"""
    from src.config import settings
    from src.services import scratch_service

    if no_gui:
        report.add("R-2 AC-58：托盘后草稿未清 + 强杀后重启仍还原",
                   "托盘后草稿未清、强杀后重启仍可还原", "SKIP", {"why": "--no-gui"})
        return

    scratch = out_dir / "scratch-58"
    scratch.mkdir(parents=True, exist_ok=True)
    settings.SCRATCH_DIR = str(scratch)
    fixture = out_dir / "fixtures" / "r10-keep.txt"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_bytes(b"KEEP-ME\n")
    pane_id = "r10tray01"
    state = out_dir / "gui-child-state.json"
    if state.exists():
        state.unlink()
    argv = [
        sys.executable, str(SCRIPT), "--child-gui",
        "--scratch", str(scratch), "--state", str(state), "--fixture", str(fixture),
        "--pane-id", pane_id, "--marker", marker, "--delay-ms", "1500",
    ]
    if os.environ.get("QT_QPA_PLATFORM", "").startswith("offscreen"):
        argv.append("--no-window")
    child = subprocess.Popen(argv, cwd=str(DESKTOP), stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)

    deadline = time.time() + 120
    payload: dict = {}
    while time.time() < deadline:
        if state.exists():
            try:
                payload = json.loads(state.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 - 状态文件可能正被写
                payload = {}
            if payload.get("stage") in {"tray", "tray-error", "fatal"}:
                break
        if child.poll() is not None:
            break
        time.sleep(0.4)

    draft = scratch / f"{PANE_FILE_PREFIX}{pane_id}{PANE_FILE_SUFFIX}"
    on_disk_after_tray = draft.read_text(encoding="utf-8") if draft.exists() else None
    try:
        listed = scratch_service.list_drafts()
    except Exception as exc:  # noqa: BLE001
        listed = {"error": f"{type(exc).__name__}: {exc}"}
    entries = listed.get("entries") if isinstance(listed, dict) else None
    pre = {
        "childStage": payload.get("stage"),
        "childError": payload.get("error"),
        "childLoadUi": payload.get("load_ui"),
        "trayObject": payload.get("trayObject"),
        "visibleBefore": payload.get("visibleBefore"),
        "visibleAfter": payload.get("visibleAfter"),
        "draftOnDiskAfterTray": on_disk_after_tray == marker,
        "draftContentMatches": on_disk_after_tray == marker,
        "entries": len(entries) if isinstance(entries, list) else None,
        "cleanExit": listed.get("cleanExit") if isinstance(listed, dict) else None,
        "listedError": listed.get("error") if isinstance(listed, dict) else None,
    }

    tray_ok = (payload.get("stage") == "tray" and pre["draftOnDiskAfterTray"]
               and payload.get("trayObject") is True
               and payload.get("visibleBefore") is True and payload.get("visibleAfter") is False
               and pre["entries"] == 1 and pre["cleanExit"] is False)
    report.add("R-2a AC-58：托盘后草稿未清", "hide_to_tray 后草稿仍在磁盘且索引条目可列出",
               "PASS" if tray_ok else "FAIL", pre)

    # 强杀（模拟崩溃）→ 新一次读盘必须仍能还原同一份完整草稿
    kill_tree(child.pid)
    try:
        child.wait(timeout=30)
    except Exception:  # noqa: BLE001 - 已 kill，等待失败不影响判定
        pass
    time.sleep(0.6)
    after_disk = draft.read_text(encoding="utf-8") if draft.exists() else None
    try:
        listed_after = scratch_service.list_drafts()
    except Exception as exc:  # noqa: BLE001
        listed_after = {"error": f"{type(exc).__name__}: {exc}"}
    entries_after = listed_after.get("entries") if isinstance(listed_after, dict) else None
    abnormal = scratch_service.is_abnormal_exit()
    after = {
        "draftContentMatches": after_disk == marker,
        "entries": len(entries_after) if isinstance(entries_after, list) else None,
        "abnormalExit": abnormal,
        "listedError": listed_after.get("error") if isinstance(listed_after, dict) else None,
        "tmp": tmp_residue(scratch),
        "childExited": child.poll() is not None,
    }
    kill_ok = (after["draftContentMatches"] and after["entries"] == 1 and abnormal is True
               and after["listedError"] is None and after["childExited"])
    report.add("R-2b AC-58：强杀后重启仍还原", "强杀后新进程读到同一份完整草稿且判定为异常退出",
               "PASS" if kill_ok else "FAIL", after)

    scratch_service.mark_clean_exit()
    clean = {"abnormalAfterClean": scratch_service.is_abnormal_exit(),
             "draftKeptAfterClean": (draft.read_text(encoding="utf-8")
                                     if draft.exists() else None) == marker,
             "fixtureUnchanged": fixture.read_bytes() == b"KEEP-ME\n"}
    clean_ok = (clean["abnormalAfterClean"] is False and clean["draftKeptAfterClean"]
                and clean["fixtureUnchanged"])
    report.add("R-2c AC-58：干净退出标记不影响草稿", "mark_clean_exit 后不再报异常退出、草稿保留",
               "PASS" if clean_ok else "FAIL", clean)


# ─────────────────────────── 父进程入口 ───────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="r10 强杀-重启复核（P6-5）")
    parser.add_argument("--rounds", type=int, default=6, help="AC-S2 强杀轮数（下限 5）")
    parser.add_argument("--marker-len", type=int, default=64, help="marker 的 x 重复长度")
    parser.add_argument("--no-gui", action="store_true", help="跳过 AC-58 的真外壳半边")
    parser.add_argument("--out", default="", help="证据目录（默认 poc/editor-probes/out/r10-<时间戳>）")
    # 子进程模式（父进程内部使用，不写进 README 用法）
    parser.add_argument("--child-drafts", action="store_true")
    parser.add_argument("--child-gui", action="store_true")
    parser.add_argument("--scratch", default="")
    parser.add_argument("--log", default="")
    parser.add_argument("--tag", default="")
    parser.add_argument("--pane-id", default="r10pane")
    parser.add_argument("--gap-ms", type=int, default=60)
    parser.add_argument("--state", default="")
    parser.add_argument("--fixture", default="")
    parser.add_argument("--marker", default="")
    parser.add_argument("--delay-ms", type=int, default=1500)
    parser.add_argument("--no-window", action="store_true")
    args = parser.parse_args()

    if args.child_drafts:
        return child_drafts(args)
    if args.child_gui:
        return child_gui(args)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else SCRIPT.parent / "out" / f"r10-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 用户目录沙箱：必须在导入 src.config.settings 之前改 APPDATA（子进程继承）
    appdata = out_dir / "appdata"
    appdata.mkdir(parents=True, exist_ok=True)
    os.environ["APPDATA"] = str(appdata)
    os.environ["LOCALAPPDATA"] = str(appdata)

    report = Report(out_dir)
    report.say(f"r10 强杀-重启复核 · 平台={os.environ.get('QT_QPA_PLATFORM', '(默认)')} · "
               f"轮数={args.rounds} · 真外壳={not args.no_gui}")
    report.say(f"证据目录：{out_dir}")

    rounds = max(5, args.rounds)
    try:
        phase_kill_rounds(report, out_dir, rounds, args.marker_len)
        phase_half_state(report, out_dir, args.marker_len)
        phase_tray_kill(report, out_dir, _marker("tray", 777, args.marker_len), args.no_gui)
    except Exception as exc:  # noqa: BLE001 - 任何未预期异常都留证
        import traceback

        traceback.print_exc()
        report.add("R0", "探针自身无未捕获异常", "FAIL", {"error": f"{type(exc).__name__}: {exc}"})

    path = report.write({"rounds": rounds, "platform": os.environ.get("QT_QPA_PLATFORM", "")})
    code = report.verdict()
    report.say(f"结果 JSON：{path}")
    report.say(f"退出码：{code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
