"""
P0-2 自动化自检：JS → 桥 → services → http_client（真实加密）→ 服务端

它回答的问题（对应计划 P0-2 验收标准）：
  - JS 调 `bridge.request(...)` 能否真的走通既有的 `http_client.py` 加密链路？
  - 白名单是否真的拦得住未登记方法？
  - Python 能否主动把事件推回 JS？
  - JS 侧到底能不能拿到 token / session_key？（应当拿不到）

## 对用户环境的保护（重要）

桌面端的会话文件在 `%APPDATA%\\JFLove\\storage\\session.json`，**本机已存在**
（说明用户当前是登录状态）。本测试会登录临时账号，若不处理会**覆盖/清空用户真实会话**。
因此测试在开始前把 `auth_service._SESSION_FILE` 重定向到 PoC 临时路径，
并在结束时校验真实文件哈希未变 —— 全程不读写用户的真实会话。

运行：
    jflove-desktop\\venv-win\\Scripts\\python.exe jflove-desktop\\poc\\bridge\\test_headless.py
    # 需要真实窗口与截图时：
    ... test_headless.py --platform=windows
    # 调试时保留临时账号与凭据：
    ... test_headless.py --keep-account

退出码：0 = 全部通过；1 = 有失败项
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_POC_DIR = _HERE.parent
_REPO_ROOT = _HERE.parents[2]
_DESKTOP_ROOT = _REPO_ROOT / "jflove-desktop"
_SERVER_PY = _REPO_ROOT / "jflove-server" / "venv-win" / "Scripts" / "python.exe"
_CRED_FILE = _HERE / ".tmp_account.json"
_TMP_SESSION = _HERE / ".tmp_session.json"
_TRAFFIC_DUMP = _HERE / "out" / "traffic_summary.json"

_parser = argparse.ArgumentParser(add_help=True)
_parser.add_argument("--platform", default="offscreen", help="Qt 平台插件名，默认 offscreen")
_parser.add_argument("--keep-account", action="store_true", help="结束后保留临时账号与凭据文件")
_parser.add_argument("--timeout", type=int, default=25, help="单个桥调用超时（秒）")
_args, _unknown = _parser.parse_known_args()

os.environ["QT_QPA_PLATFORM"] = _args.platform
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-gpu --disable-dev-shm-usage --no-sandbox --in-process-gpu",
)

for _p in (str(_POC_DIR), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import requests  # noqa: E402
from bridge import Bridge  # noqa: E402
from common.session_guard import SessionGuard  # noqa: E402
from common.web_shell import WebShell, build_app  # noqa: E402
from src.utils.session import session_manager  # noqa: E402

_PASS = "PASS"
_FAIL = "FAIL"
_results: list[tuple[str, bool, str]] = []


# ── 断言 ──────────────────────────────────────────


def check(name: str, ok: bool, detail: str = "") -> None:
    """记录一条断言结果"""
    _results.append((name, bool(ok), detail))
    print(f"[{_PASS if ok else _FAIL}] {name}" + (f" — {detail}" if detail else ""))


def info(text: str) -> None:
    """诊断信息（不计入通过率）"""
    print(f"[INFO] {text}")


# ── 用户会话保护 ──────────────────────────────────
# 实现统一在 poc/common/session_guard.py（事故记录见其模块文档）


# ── 报文录制（仅测试进程内，绝不打印报文内容） ──────


class TrafficRecorder:
    """
    给 `requests` 打桩，记录每一次 HTTP 交换的**结构**（不打印内容）。

    为什么能录到：`http_client.py` 用的是模块级 `requests.post/get/...`，
    它们最终都走 `requests.sessions.Session.request`，在这一层挂钩即可全覆盖，
    且**不需要改动 `src/` 任何代码**。
    """

    def __init__(self) -> None:
        self.records: list[dict] = []
        self._orig = requests.sessions.Session.request

    def install(self) -> None:
        outer = self

        def wrapper(session, method, url, **kwargs):
            record = {
                "method": str(method).upper(),
                "url": str(url),
                "req_json": kwargs.get("json"),
                "is_stream": bool(kwargs.get("stream")),
            }
            resp = outer._orig(session, method, url, **kwargs)
            record["status"] = resp.status_code
            if not record["is_stream"]:
                try:
                    record["resp_text"] = resp.text
                except Exception:  # noqa: BLE001 - 录制失败不影响被测逻辑
                    record["resp_text"] = ""
            outer.records.append(record)
            return resp

        requests.sessions.Session.request = wrapper

    def uninstall(self) -> None:
        requests.sessions.Session.request = self._orig


def _is_envelope(obj: object) -> bool:
    """是否 ChaCha20-Poly1305 加密信封 {nonce, ciphertext}"""
    return (
        isinstance(obj, dict)
        and isinstance(obj.get("nonce"), str)
        and isinstance(obj.get("ciphertext"), str)
        and len(obj.get("nonce", "")) > 0
    )


def resp_is_envelope(record: dict) -> bool:
    """响应体是否为加密信封"""
    try:
        return _is_envelope(json.loads(record.get("resp_text") or "{}"))
    except (TypeError, ValueError):
        return False


# ── JS 驱动 ───────────────────────────────────────


def run_js(page, expr: str, timeout_ms: int = 8000):
    """同步求值一个 JS 表达式并取回结果"""
    loop = QEventLoop()
    box: dict = {}

    def _cb(result) -> None:
        box["value"] = result
        loop.quit()

    page.runJavaScript(expr, _cb)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    return box.get("value")


def run_promise(page, expr: str, timeout_ms: int | None = None):
    """求值返回 Promise<string> 的表达式（页面把结果写进 __JF_POC_RESULT__）"""
    budget = timeout_ms if timeout_ms is not None else _args.timeout * 1000
    page.runJavaScript(
        "window.__JF_POC_RESULT__ = '';"
        f"({expr}).then(function(r){{ window.__JF_POC_RESULT__ = r; }})"
        ".catch(function(e){ window.__JF_POC_RESULT__ = 'ERR:' + e; });"
    )
    deadline = time.time() + budget / 1000.0
    while time.time() < deadline:
        value = run_js(page, "window.__JF_POC_RESULT__", timeout_ms=2000)
        if value:
            return value
        time.sleep(0.02)
    return None


def probe_page(page) -> dict:
    """页面快照"""
    raw = run_js(page, "window.__JF_POC__ ? window.__JF_POC__.probe() : ''")
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}


def wait_for(page, predicate, timeout_s: float = 20.0, interval: float = 0.05) -> bool:
    """轮询等待条件成立"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ── 临时账号 ──────────────────────────────────────


def ensure_account() -> bool:
    """确保临时账号存在（复用已有凭据文件，否则创建）"""
    if _CRED_FILE.exists():
        info(f"复用已有临时账号凭据：{_CRED_FILE}")
        return True
    if not _SERVER_PY.exists():
        print(f"找不到 server venv 解释器：{_SERVER_PY}")
        return False
    proc = subprocess.run(
        [str(_SERVER_PY), str(_HERE / "_temp_account.py"), "create"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(_REPO_ROOT),
    )
    print((proc.stdout or "").rstrip())
    if proc.returncode != 0:
        print((proc.stderr or "").rstrip())
        return False
    return True


def drop_account() -> None:
    """删除临时账号与凭据文件"""
    if not _SERVER_PY.exists():
        return
    proc = subprocess.run(
        [str(_SERVER_PY), str(_HERE / "_temp_account.py"), "drop"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(_REPO_ROOT),
    )
    print((proc.stdout or "").rstrip())


# ── 主流程 ────────────────────────────────────────


def main() -> int:
    app: QApplication = build_app()
    app.setQuitOnLastWindowClosed(False)

    # ── 0. 保护用户真实会话文件（护栏必须在任何登录之前激活） ──
    guard = SessionGuard(_TMP_SESSION)
    guard.activate()
    info(f"会话文件护栏已激活：{guard.summary}")
    check("已把会话文件重定向到 PoC 临时路径（不触碰用户真实会话）",
          str(guard.tmp_path) == str(_TMP_SESSION))

    if not ensure_account():
        print("临时账号准备失败，后续断言跳过。")
        return 1

    recorder = TrafficRecorder()
    recorder.install()

    page_js_payloads: list[str] = []

    shell = WebShell(
        page_file=str(_HERE / "web" / "index.html"),
        objects={"bridge": Bridge()},
        frameless=False,
        title="JFLove — P0-2 桥自检控制台",
    )
    shell.show()
    page = shell.view.page()

    loop = QEventLoop()
    loaded = {"ok": False}

    def _on_load(ok: bool) -> None:
        loaded["ok"] = bool(ok)
        loop.quit()

    page.loadFinished.connect(_on_load)
    QTimer.singleShot(30000, loop.quit)
    loop.exec()
    check("页面加载完成", loaded["ok"])
    if not loaded["ok"]:
        recorder.uninstall()
        return 1

    # ── 1. 桥与白名单 ──────────────────────────────
    ready = wait_for(page, lambda: bool(probe_page(page).get("bridgeReady")))
    snap = probe_page(page)
    check("桥已连通（JS 侧持有 bridge 对象）", ready, json.dumps(snap, ensure_ascii=False))

    btn_count = run_js(page, "document.querySelectorAll('[data-act]').length")
    check(
        "页面动作按钮齐备（12 个）",
        btn_count == 12,
        f"实际 {btn_count} 个：{run_js(page, 'JSON.stringify(window.__JF_POC__.probe())')}",
    )

    methods_text = run_js(page, "document.getElementById('methods').textContent")
    check(
        "白名单由 Python 同步返回并在页面渲染",
        isinstance(methods_text, str)
        and "auth.login" in methods_text
        and "demo.progress" in methods_text,
        (methods_text or "")[:90].replace("\n", " "),
    )

    # ── 2. 连通性（不出网） ────────────────────────
    ping = json.loads(run_promise(page, "window.__JF_POC__.run('ping')") or "{}")
    page_js_payloads.append(json.dumps(ping, ensure_ascii=False))
    check(
        "meta.ping 往返成功",
        ping.get("ok") is True,
        json.dumps(ping.get("result"), ensure_ascii=False)[:120],
    )

    hist = json.loads(run_promise(page, "window.__JF_POC__.run('history')") or "{}")
    page_js_payloads.append(json.dumps(hist, ensure_ascii=False))
    hist_list = hist.get("result") or []
    check(
        "meta.server_history 返回真实本地历史（纯本地文件，不出网）",
        hist.get("ok") is True
        and isinstance(hist_list, list)
        and len(hist_list) >= 1
        and all(str(u).startswith("http") for u in hist_list),
        str(hist_list),
    )

    # ── 3. 白名单负向用例 ──────────────────────────
    bad = json.loads(run_promise(page, "window.__JF_POC__.run('notWhitelisted')") or "{}")
    check(
        "未登记的方法被拒绝",
        bad.get("ok") is False and "未登记" in str(bad.get("error", "")),
        str(bad.get("error"))[:80],
    )

    bad_json = json.loads(
        run_promise(
            page,
            "window.__JF_POC__.callRaw('meta','ping','{ this is not json')",
        )
        or "{}"
    )
    check(
        "非法 JSON 入参被拒绝",
        bad_json.get("ok") is False and "参数解析失败" in str(bad_json.get("error", "")),
        str(bad_json.get("error"))[:80],
    )

    # ── 4. 真实加密会话（ECDH + 加密登录） ──────────
    pre_session = json.loads(run_promise(page, "window.__JF_POC__.run('session')") or "{}")
    page_js_payloads.append(json.dumps(pre_session, ensure_ascii=False))
    check(
        "登录前会话为空且未持有令牌",
        pre_session.get("ok") is True
        and pre_session["result"]["logged_in"] is False
        and pre_session["result"]["has_token"] is False,
        json.dumps(pre_session.get("result"), ensure_ascii=False),
    )

    kx = json.loads(run_promise(page, "window.__JF_POC__.run('keyExchange')") or "{}")
    page_js_payloads.append(json.dumps(kx, ensure_ascii=False))
    check(
        "auth.key_exchange 完成真实 ECDH 握手（session_ready）",
        kx.get("ok") is True and kx["result"]["session_ready"] is True,
        json.dumps(kx.get("result"), ensure_ascii=False),
    )

    t_login = time.time()
    login = json.loads(run_promise(page, "window.__JF_POC__.run('loginTemp')") or "{}")
    login_ms = int((time.time() - t_login) * 1000)
    page_js_payloads.append(json.dumps(login, ensure_ascii=False))
    check(
        "demo.login_temp_account 登录成功（真实加密接口）",
        login.get("ok") is True
        and login["result"]["logged_in"] is True
        and login["result"]["is_admin"] is True,
        f"{json.dumps(login.get('result'), ensure_ascii=False)} · 耗时 {login_ms}ms",
    )

    # ── 5. 令牌不许下发到 JS ───────────────────────
    session_view = json.loads(run_promise(page, "window.__JF_POC__.run('session')") or "{}")
    page_js_payloads.append(json.dumps(session_view, ensure_ascii=False))
    view_keys = set((session_view.get("result") or {}).keys())
    check(
        "会话视图刻意不含 token / session_key 字段",
        "token" not in view_keys and "session_key" not in view_keys,
        f"字段={sorted(view_keys)}",
    )
    check("Python 侧确实持有真实 JWT（只是不下发）", bool(session_manager.token),
          f"token 长度={len(session_manager.token)}")
    token_leaked = any(session_manager.token in payload for payload in page_js_payloads)
    check("返回给 JS 的全部数据里不含 JWT 原文", not token_leaked)

    # ── 6. 真实业务数据（加密接口） ────────────────
    users = json.loads(run_promise(page, "window.__JF_POC__.run('users')") or "{}")
    page_js_payloads.append(json.dumps(users, ensure_ascii=False))
    user_rows = users.get("result") or []
    usernames = [str(r.get("username")) for r in user_rows if isinstance(r, dict)]
    check(
        "users.list 取到真实用户数据（含刚建的临时账号）",
        users.get("ok") is True and len(user_rows) >= 2 and "poc_bridge_probe" in usernames,
        f"{len(user_rows)} 行：{usernames}",
    )

    disks = json.loads(run_promise(page, "window.__JF_POC__.run('disksAll')") or "{}")
    page_js_payloads.append(json.dumps(disks, ensure_ascii=False))
    check(
        "disks.all 取到真实磁盘数据",
        disks.get("ok") is True and isinstance(disks.get("result"), list),
        f"{len(disks.get('result') or [])} 个磁盘",
    )

    acc = json.loads(run_promise(page, "window.__JF_POC__.run('disksAccessible')") or "{}")
    page_js_payloads.append(json.dumps(acc, ensure_ascii=False))
    check(
        "disks.accessible 取到当前用户可访问磁盘",
        acc.get("ok") is True and isinstance(acc.get("result"), list),
        f"{len(acc.get('result') or [])} 个",
    )

    notes = json.loads(run_promise(page, "window.__JF_POC__.run('notes')") or "{}")
    page_js_payloads.append(json.dumps(notes, ensure_ascii=False))
    # 临时账号未配置笔记磁盘，服务端会返回 400 业务错误。
    # 这条断言因此检验的是**错误路径**：异常要能穿透桥、且 detail 已被正确解密。
    notes_err = str(notes.get("error", ""))
    check(
        "业务错误穿透桥并以可读文案回传（笔记磁盘未配置）",
        notes.get("ok") is False and "笔记目录未配置" in notes_err,
        notes_err[:80],
    )

    conf = json.loads(run_promise(page, "window.__JF_POC__.run('config')") or "{}")
    page_js_payloads.append(json.dumps(conf, ensure_ascii=False))
    conf_result = conf.get("result")
    check(
        "config.all 取到真实系统配置",
        conf.get("ok") is True and bool(conf_result),
        f"类型={type(conf_result).__name__} 值={json.dumps(conf_result, ensure_ascii=False)[:120]}",
    )

    # ── 7. 事件推送（Python → JS） ─────────────────
    run_js(page, "document.getElementById('events').textContent = '';")
    prog = json.loads(run_promise(page, "window.__JF_POC__.run('progress')") or "{}")
    check(
        "demo.progress 正常返回",
        prog.get("ok") is True and (prog.get("result") or {}).get("steps") == 5,
        json.dumps(prog.get("result"), ensure_ascii=False),
    )
    events_text = run_js(page, "window.__JF_POC__.eventsText()") or ""
    hits = events_text.count("demo.progress")
    check(
        "Python 主动推送的事件全部到达 JS（5/5）",
        hits == 5,
        f"页面收到 {hits} 条：{events_text.strip().splitlines()[:2]}",
    )

    # ── 8. 报文层证据 ──────────────────────────────
    records = recorder.records
    enc_req = [r for r in records if _is_envelope(r.get("req_json"))]
    enc_resp = [r for r in records if resp_is_envelope(r)]
    info(f"共录制 {len(records)} 次 HTTP 交换（加密请求 {len(enc_req)} / 加密响应 {len(enc_resp)}）")

    check("存在加密请求体（{nonce, ciphertext} 信封）", len(enc_req) >= 3, f"{len(enc_req)} 条")
    check("存在加密响应体（服务端也用信封回包）", len(enc_resp) >= 3, f"{len(enc_resp)} 条")

    err_resp = [r for r in records if int(r.get("status") or 0) >= 400]
    check(
        "错误响应同样是加密信封（宪法 §9.1.3）",
        len(err_resp) >= 1 and all(resp_is_envelope(r) for r in err_resp),
        f"{len(err_resp)} 条错误响应，全部为信封",
    )

    plain = [r for r in records if "/key-exchange" in r["url"] or "/admin-exists" in r["url"]]
    check(
        "key-exchange / admin-exists 走明文（白名单未被扩大）",
        len(plain) >= 1 and not any(_is_envelope(r.get("req_json")) for r in plain),
        f"{len(plain)} 条明文交换",
    )

    with_query = [r["url"] for r in records if "?" in r["url"]]
    check("所有请求 URL 均不带查询串（宪法 §9.1.4）", not with_query, str(with_query[:3]))

    try:
        password = json.loads(_CRED_FILE.read_text(encoding="utf-8"))["password"]
    except (OSError, ValueError, KeyError):
        password = ""
    leaked_pw = any(
        password and password in json.dumps(r, ensure_ascii=False, default=str) for r in records
    )
    check("全部报文中不出现明文口令（登录体已是密文）", not leaked_pw)

    # 报文结构摘要（只写结构，不写内容）
    try:
        _TRAFFIC_DUMP.parent.mkdir(parents=True, exist_ok=True)
        summary = [
            {
                "method": r["method"],
                "target": r["url"].split("://", 1)[-1],
                "status": r.get("status"),
                "req_is_envelope": _is_envelope(r.get("req_json")),
                "resp_is_envelope": resp_is_envelope(r),
            }
            for r in records
        ]
        _TRAFFIC_DUMP.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        info(f"报文结构摘要已写入 {_TRAFFIC_DUMP}")
    except OSError as exc:
        info(f"报文摘要写入失败：{exc}")

    # ── 9. 登出（同时清理临时会话） ────────────────
    out = json.loads(run_promise(page, "window.__JF_POC__.call('auth','logout',{})") or "{}")
    page_js_payloads.append(json.dumps(out, ensure_ascii=False))
    check(
        "auth.logout 清空会话",
        out.get("ok") is True and out["result"]["logged_in"] is False,
        json.dumps(out.get("result"), ensure_ascii=False),
    )

    # ── 10. 环境收尾校验 ───────────────────────────
    recorder.uninstall()
    shell.close()
    app.processEvents()

    real_ok, real_detail = guard.verify_unchanged()
    check("用户真实会话文件全程未被改动", real_ok, real_detail)
    guard.cleanup()
    info("已删除 PoC 临时会话文件并恢复原指向")
    if _args.keep_account:
        info(f"按 --keep-account 保留临时账号与凭据：{_CRED_FILE}")
    else:
        drop_account()

    # ── 汇总 ──────────────────────────────────────
    failed = [name for name, ok, _ in _results if not ok]
    print("\n" + "=" * 70)
    print(f"P0-2 自检：{len(_results) - len(failed)}/{len(_results)} 通过")
    if failed:
        print("失败项：")
        for name in failed:
            print(f"  - {name}")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
