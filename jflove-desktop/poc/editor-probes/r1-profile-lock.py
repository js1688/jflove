# -*- coding: utf-8 -*-
"""R-1 实测：QtWebEngine 的 profile / user-data 目录在什么时刻被创建，以及"早退"路径是否触发锁

对应设计文档 `文档记录/设计文档/v1.6.0.md` §6.6.1（判定时机）、§6.6.2（profile 锁 四条判据）、
§13.1 R-1。设计假设：单实例判定放在 `build_app()` 之后、`WebShell()` 之前 ⇒ 第二实例
**从不初始化 WebEngine profile** ⇒ 不会撞上 Chromium 对同一 user-data 目录的
`SingletonLock` / `SingletonCookie` / `SingletonSocket`。本脚本用实测检验该假设。

不修改任何项目源码。原始输出写本目录 out/ 下：
    out/r1-summary.json          结构化结论
    out/r1-A.log / r1-A.err      进程 A（真实持有 profile）完整输出
    out/r1-B.log / r1-B.err      进程 B（只建 QApplication 就退出）
    out/r1-C.log / r1-C.err      进程 C（A 运行期间创建 view）
    out/r1-P1.log / r1-P2.log    持久化 profile 对照（同一具名 profile 双进程）
    out/r1-chromium-cmdline.txt  QtWebEngineProcess.exe 命令行（含真实 --user-data-dir）
    out/r1-dir-*.json            各阶段目录快照

运行（编排三/四个进程）：
    jflove-desktop/venv-win/Scripts/python.exe jflove-desktop/poc/editor-probes/r1-profile-lock.py
单独跑某一段（调试用）：
    ... r1-profile-lock.py stage            分阶段时间线（单进程）
    ... r1-profile-lock.py stage-quick      QtWebEngineQuick.initialize() 单测
    ... r1-profile-lock.py child-a|child-b|child-c|child-p1|child-p2
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# 原始输出统一 UTF-8（Windows 控制台默认 GBK，重定向到文件后会造成乱码/非 UTF-8 日志）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:  # noqa: BLE001
        pass

OUT_DIR = Path(__file__).resolve().parent / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)
PYTHON = sys.executable

APP_NAME = "JFLove"
APP_ORG = "JFLove"
PERSISTENT_PROFILE_NAME = "jflove-r1-probe"

#: 关注的锁/单例文件命名（Chromium POSIX 用 Singleton*，Windows 走消息窗口 + 互斥体，
#: 但这里一并扫描，实测能看到什么就记什么）
LOCK_PATTERNS = ("singleton", "lock", "socket", "cookie")


def _settings_values() -> tuple[str, str]:
    """从项目 settings.py 里取出 APP_NAME / APP_ORG（保证与被测程序一致）"""
    settings = Path(__file__).resolve().parents[1].parent / "src" / "config" / "settings.py"
    name, org = APP_NAME, APP_ORG
    try:
        text = settings.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("APP_NAME"):
                name = line.split("=", 1)[1].strip().strip('"\'')
            elif line.startswith("APP_ORG"):
                org = line.split("=", 1)[1].strip().strip('"\'')
    except OSError:
        pass
    return name, org


APP_NAME, APP_ORG = _settings_values()


def data_roots() -> list[str]:
    """可能承载 profile 的目录（Windows 上 QStandardPaths 的两处 + 临时目录）"""
    home = os.path.expanduser("~")
    roots = [
        os.path.join(os.environ.get("APPDATA", home), APP_ORG, APP_NAME),        # Roaming/AppDataLocation
        os.path.join(os.environ.get("LOCALAPPDATA", home), APP_ORG, APP_NAME),    # Local/AppLocalDataLocation
        os.path.join(os.environ.get("APPDATA", home), APP_NAME),                  # 兜底：无 org 层
        os.path.join(os.environ.get("LOCALAPPDATA", home), APP_NAME),
        os.path.join(os.environ.get("TEMP", "/tmp"), "QtWebEngine"),
    ]
    return roots


def snapshot(limit: int = 4000) -> dict:
    """递归快照：相对路径 → 文件大小（目录记为 <dir>）"""
    out: dict[str, dict] = {}
    temp = os.environ.get("TEMP", "")
    for root in data_roots():
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            if temp and dirpath.count(os.sep) - root.count(os.sep) > 6:
                dirnames[:] = []
                continue
            for name in filenames + dirnames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root)
                try:
                    size = os.path.getsize(full) if os.path.isfile(full) else -1
                except OSError:
                    size = -2
                out[f"{root}::{rel}"] = {"size": size, "is_dir": os.path.isdir(full)}
                if len(out) >= limit:
                    return out
    return out


def diff(before: dict, after: dict) -> dict:
    new = {k: v for k, v in after.items() if k not in before}
    changed = {k: v for k, v in after.items() if k in before and before[k] != v}
    removed = {k: before[k] for k in before if k not in after}
    return {"new": new, "changed": changed, "removed": removed}


def lock_hits(entries: dict) -> list[str]:
    hits = []
    for key in entries:
        low = key.lower()
        if any(p in low for p in LOCK_PATTERNS):
            hits.append(key)
    return sorted(hits)


def report_stage(label: str, before: dict, after: dict, log: list) -> None:
    d = diff(before, after)
    hits = lock_hits(after)
    print(f"\n=== 阶段 {label} ===", flush=True)
    print(f"  目录条目总数：{len(after)}（新增 {len(d['new'])}，变化 {len(d['changed'])}，消失 {len(d['removed'])}）", flush=True)
    for k, v in sorted(d["new"].items())[:40]:
        print(f"    [新增] {k} (size={v['size']})", flush=True)
    for k, v in sorted(d["changed"].items())[:20]:
        print(f"    [变化] {k} {before[k]} -> {v}", flush=True)
    print(f"  命中锁/singleton 命名模式的条目：{hits if hits else '（无）'}", flush=True)
    log.append({"stage": label, "total": len(after), "new": sorted(d["new"]), "changed": sorted(d["changed"]),
                "lock_like_entries": hits})


# ─────────────────────────────────────────────────────────────────────────────
# 单进程分阶段时间线
# ─────────────────────────────────────────────────────────────────────────────
def mode_stage(quick: bool = False) -> int:
    timeline: list[dict] = []
    base = snapshot()
    print(f"基线：目录条目 {len(base)} 个；观察根目录 {data_roots()}", flush=True)

    print("\n[stage 0] 基线（未 import PySide6）完成", flush=True)
    report_stage("0-基线", base, base, timeline)

    if quick:
        from PySide6.QtWidgets import QApplication  # noqa: E402

        app = QApplication(sys.argv)
        app.setApplicationName(APP_NAME)
        app.setOrganizationName(APP_ORG)
        s1 = snapshot()
        report_stage("1-QApplication 之后（Quick 路线）", base, s1, timeline)
        try:
            from PySide6.QtWebEngineQuick import QtWebEngineQuick  # noqa: E402

            s2 = snapshot()
            report_stage("2-import QtWebEngineQuick 之后", s1, s2, timeline)
            QtWebEngineQuick.initialize()
            s3 = snapshot()
            report_stage("3-QtWebEngineQuick.initialize() 之后", s2, s3, timeline)
        except Exception as exc:  # noqa: BLE001
            print(f"!! QtWebEngineQuick 路线不可用：{type(exc).__name__}: {exc}", flush=True)
            timeline.append({"stage": "quick-unavailable", "error": f"{type(exc).__name__}: {exc}"})
        (OUT_DIR / "r1-timeline-quick.json").write_text(
            json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _ = app
        return 0

    import PySide6.QtWebEngineWidgets  # noqa: E402,F401

    s1 = snapshot()
    report_stage("1-import QtWebEngineWidgets 之后", base, s1, timeline)

    from PySide6.QtCore import QUrl  # noqa: E402
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile  # noqa: E402
    from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
    from PySide6.QtWidgets import QApplication  # noqa: E402

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORG)
    s2 = snapshot()
    report_stage("2-QApplication()+setApplicationName/Organization 之后", s1, s2, timeline)

    prof = QWebEngineProfile.defaultProfile()
    print(
        f"\n默认 profile: storageName={prof.storageName()!r} offTheRecord={prof.isOffTheRecord()} "
        f"persistent={prof.persistentStoragePath()!r} cache={prof.cachePath()!r}",
        flush=True,
    )
    s3 = snapshot()
    report_stage("3-QWebEngineProfile.defaultProfile() 触碰之后", s2, s3, timeline)

    page = QWebEnginePage()
    s4 = snapshot()
    report_stage("4-QWebEnginePage() 之后", s3, s4, timeline)

    view = QWebEngineView()
    view.resize(500, 300)
    s5 = snapshot()
    report_stage("5-QWebEngineView() 之后（未 show）", s4, s5, timeline)

    view.show()
    html = OUT_DIR / "r1-page.html"
    html.write_text("<!doctype html><meta charset=utf-8><title>r1</title><p>r1 probe</p>", encoding="utf-8")
    state = {"ok": None}

    def on_load(ok: bool) -> None:
        state["ok"] = ok

    view.loadFinished.connect(on_load)
    view.setUrl(QUrl.fromLocalFile(str(html)))
    deadline = time.time() + 20
    while state["ok"] is None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.05)
    s6 = snapshot()
    report_stage("6-show()+loadFinished 之后", s5, s6, timeline)

    js_done = {"v": False}
    view.page().runJavaScript(
        "try{localStorage.setItem('r1probe','1');'ok'}catch(e){'err:'+e}",
        lambda v: (print(f"  localStorage 写入结果={v}", flush=True), js_done.__setitem__("v", True)),
    )
    deadline = time.time() + 10
    while not js_done["v"] and time.time() < deadline:
        app.processEvents()
        time.sleep(0.05)
    time.sleep(1.0)
    app.processEvents()
    s7 = snapshot()
    report_stage("7-页面写 localStorage 之后", s6, s7, timeline)

    print("\n[stage 8] 具名持久化 profile（对照组）", flush=True)
    pprof = QWebEngineProfile(PERSISTENT_PROFILE_NAME)
    print(
        f"  持久化 profile: offTheRecord={pprof.isOffTheRecord()} persistent={pprof.persistentStoragePath()!r} "
        f"cache={pprof.cachePath()!r}",
        flush=True,
    )
    pview = QWebEngineView()
    pview.setPage(QWebEnginePage(pprof, pview))
    pview.resize(400, 250)
    pview.show()
    pstate = {"ok": None}
    pview.loadFinished.connect(lambda ok: pstate.__setitem__("ok", ok))
    pview.setUrl(QUrl.fromLocalFile(str(html)))
    deadline = time.time() + 20
    while pstate["ok"] is None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.05)
    time.sleep(1.0)
    app.processEvents()
    s8 = snapshot()
    report_stage("8-具名持久化 profile + view 之后", s7, s8, timeline)
    timeline.append({
        "stage": "profile-paths",
        "default": {
            "storage_name": prof.storageName(),
            "off_the_record": prof.isOffTheRecord(),
            "persistent": prof.persistentStoragePath(),
            "cache": prof.cachePath(),
        },
        "named": {
            "name": PERSISTENT_PROFILE_NAME,
            "off_the_record": pprof.isOffTheRecord(),
            "persistent": pprof.persistentStoragePath(),
            "cache": pprof.cachePath(),
        },
    })
    (OUT_DIR / "r1-timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n时间线已写入 {OUT_DIR / 'r1-timeline.json'}", flush=True)
    _ = page
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# 子进程模式
# ─────────────────────────────────────────────────────────────────────────────
def _base_app(name_only: bool = False):
    from PySide6.QtWidgets import QApplication  # noqa: E402

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORG)
    from PySide6.QtWebEngineCore import QWebEngineProfile  # noqa: E402

    prof = QWebEngineProfile.defaultProfile()
    print(
        f"PID={os.getpid()} profile storageName={prof.storageName()!r} "
        f"offTheRecord={prof.isOffTheRecord()} persistent={prof.persistentStoragePath()!r}",
        flush=True,
    )
    print(f"user_data_dirs={data_roots()}", flush=True)
    return app, prof


def mode_child_a() -> int:
    import time as _t

    from PySide6.QtCore import QTimer, QUrl  # noqa: E402
    from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402

    hold = int(os.environ.get("R1_HOLD_SECONDS", "20"))
    app, _prof = _base_app()
    view = QWebEngineView()
    view.resize(600, 400)
    mark = {"i": 0, "start": _t.time()}
    html = OUT_DIR / "r1-child-page.html"
    html.write_text("<!doctype html><meta charset=utf-8><p>child a</p>", encoding="utf-8")

    def read_dir() -> dict:
        return {k: v for k, v in snapshot().items()}

    def on_load(ok: bool) -> None:
        print(f"A_LOAD ok={ok}", flush=True)
        print(f"A_VIEW_READY at t={_t.time() - mark['start']:.2f}s", flush=True)
        print(f"A_LOCK_LIKE={lock_hits(snapshot())}", flush=True)

    def tick() -> None:
        mark["i"] += 1
        print(f"A_TICK n={mark['i']} t={_t.time() - mark['start']:.2f}s alive=1", flush=True)

    view.loadFinished.connect(on_load)
    view.show()
    view.setUrl(QUrl.fromLocalFile(str(html)))
    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(2000)
    QTimer.singleShot(hold * 1000, app.quit)
    app.exec()
    print("A_EXIT_OK", flush=True)
    print(f"A_FINAL_LOCK_LIKE={lock_hits(snapshot())}", flush=True)
    return 0


def mode_child_b() -> int:
    from PySide6.QtCore import QTimer  # noqa: E402

    app, _prof = _base_app(name_only=True)
    print("B_APP_ONLY_READY", flush=True)
    print(f"B_LOCK_LIKE={lock_hits(snapshot())}", flush=True)
    QTimer.singleShot(1200, app.quit)
    app.exec()
    print("B_EXIT_OK", flush=True)
    return 0


def mode_child_c() -> int:
    import traceback

    from PySide6.QtCore import QTimer, QUrl  # noqa: E402
    from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402

    app, _prof = _base_app()
    html = OUT_DIR / "r1-child-page.html"
    if not html.exists():
        html.write_text("<!doctype html><meta charset=utf-8><p>child c</p>", encoding="utf-8")
    try:
        view = QWebEngineView()
        view.resize(600, 400)
        view.loadFinished.connect(lambda ok: print(f"C_LOAD ok={ok}", flush=True))
        view.show()
        view.setUrl(QUrl.fromLocalFile(str(html)))
        QTimer.singleShot(8000, lambda: (print("C_VIEW_ALIVE_8S", flush=True), app.quit()))
        app.exec()
        print(f"C_LOCK_LIKE={lock_hits(snapshot())}", flush=True)
        print("C_EXIT_OK", flush=True)
        return 0
    except BaseException:  # noqa: BLE001
        print("C_ERROR", flush=True)
        traceback.print_exc()
        return 3


def _persistent_view(label: str, hold: int):
    import time as _t

    from PySide6.QtCore import QTimer, QUrl  # noqa: E402
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile  # noqa: E402
    from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402

    app, _prof = _base_app()
    prof = QWebEngineProfile(PERSISTENT_PROFILE_NAME)
    print(
        f"{label}_PROFILE persistent={prof.persistentStoragePath()!r} "
        f"offTheRecord={prof.isOffTheRecord()}",
        flush=True,
    )
    view = QWebEngineView()
    view.setPage(QWebEnginePage(prof, view))
    view.resize(600, 400)
    html = OUT_DIR / "r1-child-page.html"
    if not html.exists():
        html.write_text("<!doctype html><meta charset=utf-8><p>child p</p>", encoding="utf-8")
    start = _t.time()
    view.loadFinished.connect(lambda ok: print(f"{label}_LOAD ok={ok}", flush=True))
    view.show()
    view.setUrl(QUrl.fromLocalFile(str(html)))
    print(f"{label}_VIEW_READY t={_t.time() - start:.2f}s", flush=True)
    print(f"{label}_LOCK_LIKE={lock_hits(snapshot())}", flush=True)
    ticks = {"n": 0}

    def tick() -> None:
        ticks["n"] += 1
        print(f"{label}_TICK n={ticks['n']} t={_t.time() - start:.2f}s alive=1", flush=True)

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(2000)
    QTimer.singleShot(hold * 1000, app.quit)
    app.exec()
    print(f"{label}_EXIT_OK ticks={ticks['n']}", flush=True)
    return 0


def mode_child_p1() -> int:
    return _persistent_view("P1", int(os.environ.get("R1_HOLD_SECONDS", "20")))


def mode_child_p2() -> int:
    return _persistent_view("P2", 5)


# ─────────────────────────────────────────────────────────────────────────────
# 编排
# ─────────────────────────────────────────────────────────────────────────────
def _run_child(mode: str, env_extra: dict | None = None, timeout: int = 120) -> dict:
    log = OUT_DIR / f"r1-{mode}.log"
    err = OUT_DIR / f"r1-{mode}.err"
    env = dict(os.environ)
    env.update(env_extra or {})
    # 子进程原始输出也要 UTF-8，否则编排侧解析会乱码
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    start = time.time()
    with open(log, "wb") as fout, open(err, "wb") as ferr:
        try:
            proc = subprocess.run(
                [PYTHON, str(Path(__file__).resolve()), mode],
                stdout=fout, stderr=ferr, env=env, timeout=timeout,
            )
            code = proc.returncode
            timed_out = False
        except subprocess.TimeoutExpired:
            code = None
            timed_out = True
    return {
        "mode": mode,
        "exit_code": code,
        "timed_out": timed_out,
        "seconds": round(time.time() - start, 2),
        "log": str(log),
        "err": str(err),
        "stdout": log.read_text(encoding="utf-8", errors="replace"),
        "stderr": err.read_text(encoding="utf-8", errors="replace"),
    }


def _wait_for_marker(path: Path, marker: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if marker in path.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            pass
        time.sleep(0.3)
    return False


def _chromium_cmdline() -> str:
    cmd = (
        "Get-CimInstance Win32_Process -Filter \"Name='QtWebEngineProcess.exe'\" | "
        "ForEach-Object { $_.ProcessId.ToString() + ' :: ' + $_.CommandLine }"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, timeout=60,
        )
        return proc.stdout.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return f"<查询失败: {exc}>"


def _start_child(mode: str, env_extra: dict | None = None):
    log = OUT_DIR / f"r1-{mode}.log"
    err = OUT_DIR / f"r1-{mode}.err"
    env = dict(os.environ)
    env.update(env_extra or {})
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    fout = open(log, "wb")
    ferr = open(err, "wb")
    proc = subprocess.Popen(
        [PYTHON, str(Path(__file__).resolve()), mode],
        stdout=fout, stderr=ferr, env=env,
    )
    return {"proc": proc, "fout": fout, "ferr": ferr, "log": log, "err": err, "mode": mode}


def _stop_child(handle: dict) -> int:
    proc = handle["proc"]
    try:
        code = proc.wait(timeout=90)
    except subprocess.TimeoutExpired:
        proc.kill()
        code = proc.wait()
    handle["fout"].close()
    handle["ferr"].close()
    return code


def mode_orchestrate() -> int:
    summary: dict = {"started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "python": PYTHON,
                     "user_data_roots": data_roots(), "steps": {}}
    print("== R-1 编排开始 ==", flush=True)
    print(f"观察根目录：{data_roots()}", flush=True)

    base = snapshot()
    (OUT_DIR / "r1-dir-baseline.json").write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[1] 基线目录条目 {len(base)}；锁类命中 {lock_hits(base)}", flush=True)
    summary["steps"]["baseline"] = {"total": len(base), "lock_like": lock_hits(base)}

    print("[2] 启动进程 A（QApplication + QWebEngineView，保持 40 s）", flush=True)
    a = _start_child("child-a", {"R1_HOLD_SECONDS": "40"})
    ready = _wait_for_marker(a["log"], "A_VIEW_READY", 90)
    print(f"    A 就绪：{ready}", flush=True)
    q = _chromium_cmdline()
    (OUT_DIR / "r1-chromium-cmdline.txt").write_text(q, encoding="utf-8")
    print("    QtWebEngineProcess 命令行已写入 out/r1-chromium-cmdline.txt", flush=True)
    holding = snapshot()
    (OUT_DIR / "r1-dir-holding.json").write_text(json.dumps(holding, ensure_ascii=False, indent=2), encoding="utf-8")
    d_hold = diff(base, holding)
    summary["steps"]["a_holding"] = {
        "ready": ready,
        "new_entries": sorted(d_hold["new"]),
        "lock_like": lock_hits(holding),
    }
    print(f"    A 持有 profile 期间新增条目 {len(d_hold['new'])}：{sorted(d_hold['new'])[:20]}", flush=True)
    print(f"    锁类命中：{lock_hits(holding)}", flush=True)

    print("[3] 进程 B（只建 QApplication，不建 view/page）", flush=True)
    b = _run_child("child-b", {"R1_HOLD_SECONDS": "2"}, timeout=90)
    after_b = snapshot()
    d_b = diff(holding, after_b)
    summary["steps"]["b"] = {
        "exit_code": b["exit_code"], "timed_out": b["timed_out"], "seconds": b["seconds"],
        "stdout": b["stdout"], "stderr": b["stderr"],
        "new_entries_after_b": sorted(d_b["new"]), "lock_like_after_b": lock_hits(after_b),
    }
    print(f"    B 退出码 {b['exit_code']}，耗时 {b['seconds']}s；B 之后新增条目 {sorted(d_b['new'])}", flush=True)
    print(f"    B stderr：{b['stderr'].strip()[:400]!r}", flush=True)

    alive_before_c = a["log"].read_text(encoding="utf-8", errors="replace").count("A_TICK")
    print(f"[4] 进程 C（A 运行期间创建 view）；A 当前 tick 数 {alive_before_c}", flush=True)
    c = _run_child("child-c", timeout=90)
    alive_after_c = a["log"].read_text(encoding="utf-8", errors="replace").count("A_TICK")
    after_c = snapshot()
    d_c = diff(after_b, after_c)
    summary["steps"]["c"] = {
        "exit_code": c["exit_code"], "timed_out": c["timed_out"], "seconds": c["seconds"],
        "stdout": c["stdout"], "stderr": c["stderr"],
        "new_entries_after_c": sorted(d_c["new"]), "lock_like_after_c": lock_hits(after_c),
        "a_ticks_before": alive_before_c, "a_ticks_after": alive_after_c,
    }
    print(f"    C 退出码 {c['exit_code']}，耗时 {c['seconds']}s", flush=True)
    print(f"    C stderr：{c['stderr'].strip()[:600]!r}", flush=True)
    print(f"    C 之后新增条目 {sorted(d_c['new'])[:30]}", flush=True)
    print(f"    A 是否存活（tick 数）：{alive_before_c} -> {alive_after_c}", flush=True)

    print("[5] 等 A 自行退出", flush=True)
    a_code = _stop_child(a)
    a_out = a["log"].read_text(encoding="utf-8", errors="replace")
    a_err = a["err"].read_text(encoding="utf-8", errors="replace")
    summary["steps"]["a_final"] = {
        "exit_code": a_code,
        "ticks": a_out.count("A_TICK"),
        "exit_ok": "A_EXIT_OK" in a_out,
        "lock_like_final": [ln for ln in a_out.splitlines() if "A_FINAL_LOCK_LIKE" in ln],
        "stderr_tail": a_err[-2000:],
    }
    print(f"    A 退出码 {a_code}，tick 总数 {a_out.count('A_TICK')}，正常退出={'A_EXIT_OK' in a_out}", flush=True)

    print("[6] 持久化 profile 对照：P1 持有，P2 同时打开同一具名 profile", flush=True)
    p1 = _start_child("child-p1", {"R1_HOLD_SECONDS": "25"})
    p1_ready = _wait_for_marker(p1["log"], "P1_VIEW_READY", 90)
    dir_p1 = snapshot()
    p2 = _run_child("child-p2", timeout=90)
    p1_after = p1["log"].read_text(encoding="utf-8", errors="replace")
    dir_p2 = snapshot()
    summary["steps"]["persistent_pair"] = {
        "p1_ready": p1_ready,
        "p2_exit_code": p2["exit_code"], "p2_timed_out": p2["timed_out"], "p2_seconds": p2["seconds"],
        "p2_stdout": p2["stdout"], "p2_stderr": p2["stderr"],
        "p1_ticks_before_p2": p1_after.count("P1_TICK"),
        "lock_like_after_p1": lock_hits(dir_p1),
        "new_entries_after_p2": sorted(diff(dir_p1, dir_p2)["new"]),
        "lock_like_after_p2": lock_hits(dir_p2),
    }
    print(f"    P1 就绪 {p1_ready}；P2 退出码 {p2['exit_code']}，耗时 {p2['seconds']}s", flush=True)
    print(f"    P2 stderr：{p2['stderr'].strip()[:600]!r}", flush=True)
    print(f"    P2 之后新增条目 {sorted(diff(dir_p1, dir_p2)['new'])[:30]}", flush=True)
    p1_code = _stop_child(p1)
    p1_out = p1["log"].read_text(encoding="utf-8", errors="replace")
    summary["steps"]["persistent_pair"]["p1_exit_code"] = p1_code
    summary["steps"]["persistent_pair"]["p1_ticks_total"] = p1_out.count("P1_TICK")
    print(f"    P1 退出码 {p1_code}，tick 总数 {p1_out.count('P1_TICK')}", flush=True)

    # 全量扫描：所有观察根目录里出现过 Singleton* 的文件（无论阶段）
    final = snapshot()
    summary["steps"]["final"] = {"total": len(final), "lock_like": lock_hits(final)}
    summary["chromium_cmdline"] = q
    (OUT_DIR / "r1-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n== 编排结束，汇总写入 {OUT_DIR / 'r1-summary.json'} ==", flush=True)
    print(f"最终锁类命中：{lock_hits(final)}", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", nargs="?", default="orchestrate",
                        choices=["orchestrate", "stage", "stage-quick", "child-a", "child-b",
                                 "child-c", "child-p1", "child-p2"])
    args = parser.parse_args()
    if args.mode == "orchestrate":
        return mode_orchestrate()
    if args.mode == "stage":
        return mode_stage(quick=False)
    if args.mode == "stage-quick":
        return mode_stage(quick=True)
    return {
        "child-a": mode_child_a,
        "child-b": mode_child_b,
        "child-c": mode_child_c,
        "child-p1": mode_child_p1,
        "child-p2": mode_child_p2,
    }[args.mode]()


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    sys.exit(main())
