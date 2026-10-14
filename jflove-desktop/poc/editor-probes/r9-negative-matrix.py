#!/usr/bin/env python3
"""
r9 负向对照变异矩阵（v1.6.0 Phase 6 · P6-4）

做一件事：**故意把源码改坏，然后要求对应判据必须失败**。
判据在干净源码上通过（基线）而变异后失败 ⇒ 判据真的在管这件事；
变异后仍然通过 ⇒ 该判据是「空转」的，必须补强。

设计要点
- 每个用例只改一处（字符串替换 / 追加 / 新建文件），**改完立刻按字节还原**，
  并以 sha256 复验；脚本结束再比对一次 ``git status --porcelain`` 作为兜底证据。
- 变异锚点必须**恰好命中 1 次**，否则视为脚本自身错误（exit 2）——
  拒绝「变异其实没生效」造成的假绿。
- 退出码：0 = 全部被抓且全部还原；1 = 有用例没被抓；2 = 脚本/环境错误
  （锚点未命中、还原失败、判据超时）。
- ``--baseline`` 会在变异前先跑一次判据、要求它**通过**（默认关闭，避免重复耗时）。

用法：
    python jflove-desktop/poc/editor-probes/r9-negative-matrix.py
    python jflove-desktop/poc/editor-probes/r9-negative-matrix.py --baseline
    python jflove-desktop/poc/editor-probes/r9-negative-matrix.py --only ATOMIC-RETRY
    python jflove-desktop/poc/editor-probes/r9-negative-matrix.py --list
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

SCRIPT = Path(__file__).resolve()
DESKTOP = SCRIPT.parents[2]
REPO = SCRIPT.parents[3]
SERVER = REPO / "jflove-server"
WEB = REPO / "jflove-web"
DESKTOP_PY = DESKTOP / "venv-win" / "Scripts" / "python.exe"
SERVER_PY = SERVER / "venv-win" / "Scripts" / "python.exe"
NODE = shutil.which("node") or "node"
WEB_VITEST = "node_modules/vitest/vitest.mjs"

EDITOR_SERVICE = "jflove-desktop/src/services/editor_service.py"
TEXT_DETECT = "jflove-desktop/src/services/text_detect_service.py"
CONFIG_CONTROLLER = "jflove-server/src/controllers/config_controller.py"
HTTP_CLIENT = "jflove-web/src/utils/http-client.ts"


def _d_pytest(*nodes: str) -> tuple[str, ...]:
    """桌面端单测判据（用模块自带 venv，禁 pyc / cache 落盘）"""
    return (str(DESKTOP_PY), "-m", "pytest", *nodes, "-q", "-p", "no:cacheprovider")


def _s_pytest(*nodes: str) -> tuple[str, ...]:
    """服务端单测判据（同上；服务端必须用自己的 venv，系统 python 缺 aiosqlite）"""
    return (str(SERVER_PY), "-m", "pytest", *nodes, "-q", "-p", "no:cacheprovider")


def _web_vitest(target: str) -> tuple[str, ...]:
    """Web 端 vitest 判据（直接走 node + vitest 入口，绕开 npm 批处理包装）"""
    return (NODE, WEB_VITEST, "run", target)


@dataclass(frozen=True)
class Case:
    """一条变异用例：把 ``rel`` 改坏，要求 ``argv`` 判据**失败**。"""

    id: str
    ac: str
    why: str
    op: str  # patch | append | create
    rel: str
    cwd: str
    argv: tuple[str, ...]
    old: str = ""
    new: str = ""
    content: str = ""
    timeout: int = 240
    tags: tuple[str, ...] = field(default_factory=tuple)


CASES: tuple[Case, ...] = (
    Case(
        id="ATOMIC-CLEANUP",
        ac="AC-S1 / AC-41④",
        why="失败路径不再清理临时文件 ⇒ 「原文件不变 + 目录无残留」判据必须红",
        op="patch",
        rel=EDITOR_SERVICE,
        cwd="jflove-desktop",
        old="    except BaseException:\n        _silent_unlink(tmp_path)\n        raise\n",
        new="    except BaseException:\n        raise  # MUTATION：故意不清理临时文件\n",
        argv=_d_pytest(
            "tests/test_editor_service_v1_6_0.py::TestAtomicWrite"
            "::test_failure_during_write_keeps_original",
            "tests/test_editor_service_v1_6_0.py::TestAtomicWrite"
            "::test_failure_during_replace_keeps_original",
        ),
    ),
    Case(
        id="ATOMIC-RETRY",
        ac="AC-S1",
        why="os.replace 不再重试 ⇒ 「前两次失败、第三次成功」判据必须红",
        op="patch",
        rel=EDITOR_SERVICE,
        cwd="jflove-desktop",
        old="    for attempt in range(_REPLACE_RETRIES):\n",
        new="    for attempt in range(1):  # MUTATION：故意不重试\n",
        argv=_d_pytest(
            "tests/test_editor_service_v1_6_0.py::TestAtomicWrite"
            "::test_replace_retries_then_succeeds"
        ),
    ),
    Case(
        id="CONFLICT-GUARD",
        ac="AC-41③",
        why="关掉乐观并发校验 ⇒ 「外部已改则拒绝覆盖」判据必须红",
        op="patch",
        rel=EDITOR_SERVICE,
        cwd="jflove-desktop",
        old="        if current is not None and current != expected_sha256:\n",
        new="        if False:  # MUTATION：故意关闭乐观并发校验\n",
        argv=_d_pytest(
            "tests/test_editor_service_v1_6_0.py::TestRoundTripPreserve"
            "::test_expected_sha256_conflict_raises_and_keeps_file"
        ),
    ),
    Case(
        id="BOM-DROP",
        ac="AC-20",
        why="写回不再补 BOM ⇒ 「utf-8-bom 往返逐字节相同」判据必须红",
        op="patch",
        rel=EDITOR_SERVICE,
        cwd="jflove-desktop",
        old="    if prefix and (bom is None or bom):\n",
        new="    if False:  # MUTATION：故意丢 BOM\n",
        argv=_d_pytest(
            "tests/test_editor_service_v1_6_0.py::TestRoundTripPreserve"
            "::test_utf8_bom_crlf_round_trip_is_byte_identical"
        ),
    ),
    Case(
        id="PROBE-GUARD",
        ac="AC-S3",
        why="read_text 不再校验 probe 通过 ⇒ 「probe 与读取结构隔离」判据必须红",
        op="patch",
        rel=TEXT_DETECT,
        cwd="jflove-desktop",
        old="    if not probe_result.ok:\n",
        new="    if False:  # MUTATION：故意取消 probe 前置校验\n",
        argv=_d_pytest(
            "tests/test_text_detect_service_v1_6_0.py::TestEdgeCasesAndIsolation"
            "::test_read_text_requires_passed_probe"
        ),
    ),
    Case(
        id="M4-XFAIL-EXPIRY",
        ac="AC-41④ / M-4(BL-21)",
        why=(
            "模拟 M-4 已修（unlink 前先去掉只读位）⇒ strict xfail 必然 XPASS 变红，"
            "证明「M-4 未修」的留证标记仍是活的、修复后会被强制提醒"
        ),
        op="patch",
        rel=EDITOR_SERVICE,
        cwd="jflove-desktop",
        old="    try:\n        os.unlink(path)\n    except OSError:\n        pass\n",
        new=(
            "    try:\n        os.chmod(path, 0o666)  # MUTATION：模拟 M-4 修复\n"
            "        os.unlink(path)\n    except OSError:\n        pass\n"
        ),
        argv=_d_pytest(
            "tests/test_v160_phase6_save.py::TestFailureSemantics"
            "::test_只读目标原子写失败后不留临时文件"
        ),
    ),
    Case(
        id="HOTKEY-STATIC",
        ac="AC-8",
        why="源码里出现全局热键注册 API ⇒ 「全仓零命中全局热键」判据必须红",
        op="create",
        rel="jflove-desktop/src/_negctl_r9_hotkey.py",
        cwd="jflove-desktop",
        content='"""MUTATION：只在本用例存活期间存在，用来验证 AC-8 判据有牙。"""\n'
        # 令牌拆成两段拼接：本脚本自身也在 AC-8 的扫描面内，写字面量会造成自命中
        '"Register" + "HotKey(0, 1, 0, 0)\n',
        argv=_d_pytest("tests/test_v160_phase6_hotkeys.py"),
    ),
    Case(
        id="SERVER-B8",
        ac="AC-B8",
        why="把测试依赖写进运行时 requirements ⇒ 「运行时依赖零命中测试工具」判据必须红",
        op="append",
        rel="jflove-server/requirements.txt",
        cwd="jflove-server",
        content="pytest-cov==7.1.0  # MUTATION",
        argv=_s_pytest(
            "tests/test_v160_phase6_debt.py::TestRuntimeRequirementsExcludeTestDeps"
        ),
    ),
    Case(
        id="SERVER-B2",
        ac="AC-B2",
        why="把旧错措辞写回 GET /config 描述 ⇒ 「旧措辞已消失」判据必须红",
        op="patch",
        rel=CONFIG_CONTROLLER,
        cwd="jflove-server",
        old="配置项键值对映射（`{key: value}`）",
        new="配置项列表（每项含 `key`、`value`）",
        argv=_s_pytest(
            "tests/test_v160_phase6_debt.py::TestConfigOpenApiMatchesImplementation"
            "::test_get_config_旧错措辞已消失"
        ),
    ),
    Case(
        id="WEB-ENVELOPE-502",
        ac="AC-B3",
        why="信封解不开时不再报错而是当明文返回 ⇒ 「合成 502」判据必须红",
        op="patch",
        rel=HTTP_CLIENT,
        cwd="jflove-web",
        old=(
            "  } catch {\n"
            "    throw new ApiError(502, '响应解密失败（会话密钥不匹配或响应被篡改）');\n"
            "  }\n"
        ),
        new="  } catch {\n    return body as T; // MUTATION：故意吞掉解密失败\n  }\n",
        argv=_web_vitest("tests/utils/http-client.test.ts"),
        timeout=600,
        tags=("慢",),
    ),
)


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_status(paths: tuple[str, ...] = ()) -> str:
    """只统计**变异目标文件**的状态：并行协作者的其它新增文件不应算作污染。"""
    argv = ["git", "status", "--porcelain"]
    if paths:
        argv += ["--", *paths]
    proc = subprocess.run(
        argv,
        cwd=str(REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_env(),
    )
    return proc.stdout.strip()


def _run(argv: tuple[str, ...], cwd: Path, timeout: int) -> tuple[int, str, float]:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            list(argv),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_env(),
        )
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") + (exc.stderr or "")
        if isinstance(out, bytes):  # pragma: no cover - 平台差异兜底
            out = out.decode("utf-8", "replace")
        return -1, out, time.monotonic() - started
    return proc.returncode, (proc.stdout or "") + (proc.stderr or ""), time.monotonic() - started


def _tail(text: str, lines: int = 14) -> str:
    rows = [row for row in text.splitlines() if row.strip()]
    return "\n".join(rows[-lines:])


def _apply(case: Case) -> tuple[Path, bytes | None]:
    """施加变异；返回 (目标路径, 原始字节)。``create`` 用例原始字节为 None。"""
    target = REPO / case.rel
    if case.op == "create":
        if target.exists():
            raise RuntimeError(f"变异文件已存在，拒绝覆盖：{target}")
        target.write_text(case.content, encoding="utf-8")
        return target, None

    original = target.read_bytes()
    text = original.decode("utf-8")
    if case.op == "append":
        text = text.rstrip("\n") + "\n" + case.content + "\n"
    elif case.op == "patch":
        old, new = case.old, case.new
        if text.count(old) != 1 and "\r\n" in text:
            # 目标文件是 CRLF（如 jflove-web 的 TS 源）：锚点按文件实际换行符重排
            old = old.replace("\n", "\r\n")
            new = new.replace("\n", "\r\n")
        hits = text.count(old)
        if hits != 1:
            raise RuntimeError(
                f"变异锚点命中 {hits} 次（要求恰好 1 次）：{old.splitlines()[0][:60]!r}"
            )
        text = text.replace(old, new, 1)
    else:  # pragma: no cover - 定义期错误
        raise RuntimeError(f"未知 op：{case.op}")
    target.write_bytes(text.encode("utf-8"))
    return target, original


def _restore(case: Case, target: Path, original: bytes | None) -> str:
    """还原并复验；返回可读的还原结论。"""
    if case.op == "create":
        if target.exists():
            target.unlink()
        return "已删除新建文件" if not target.exists() else "还原失败：新建文件仍在"

    assert original is not None
    target.write_bytes(original)
    now = _sha256(target.read_bytes())
    want = _sha256(original)
    return "sha 一致" if now == want else f"还原失败：sha 不一致（{now[:8]} != {want[:8]}）"


def main() -> int:
    parser = argparse.ArgumentParser(description="r9 负向对照变异矩阵")
    parser.add_argument("--only", nargs="*", default=None, help="只跑指定 id")
    parser.add_argument("--list", action="store_true", help="列出用例后退出")
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="变异前先跑一遍判据，要求它通过（更严谨，耗时约翻倍）",
    )
    args = parser.parse_args()

    cases = list(CASES)
    if args.only:
        wanted = {item.upper() for item in args.only}
        cases = [case for case in cases if case.id.upper() in wanted]
        if not cases:
            print(f"没有匹配的用例：{sorted(wanted)}")
            return 2

    if args.list:
        for case in cases:
            print(f"{case.id:20s} {case.ac:20s} {case.rel}")
            print(f"{'':20s} 依据：{case.why}")
            print(f"{'':20s} 判据：{' '.join(case.argv[-3:])}（cwd={case.cwd}）")
        return 0

    for label, path in (("桌面端 venv", DESKTOP_PY), ("服务端 venv", SERVER_PY)):
        if not path.is_file():
            print(f"[环境错误] {label} 不存在：{path}")
            return 2

    targets = tuple(dict.fromkeys(case.rel for case in cases))
    before = _git_status(targets)
    print("=" * 78)
    print("r9 负向对照变异矩阵：故意改坏源码 ⇒ 判据必须失败")
    print(f"仓库根：{REPO}")
    print(f"用例数：{len(cases)}（baseline={'on' if args.baseline else 'off'}）")
    print("=" * 78)

    caught: list[str] = []
    missed: list[str] = []
    errors: list[str] = []

    for case in cases:
        label = f"{case.id}（{case.ac}）"
        print(f"\n── {label} :: {case.rel}")
        print(f"   依据：{case.why}")

        baseline_ok: str = "未跑"
        if args.baseline:
            code, out, cost = _run(case.argv, REPO / case.cwd, case.timeout)
            if code != 0:
                errors.append(case.id)
                print(f"   [错误] 基线判据在干净源码上就没过（exit {code}, {cost:.1f}s）")
                print(_tail(out))
                continue
            baseline_ok = f"通过({cost:.1f}s)"

        target_path: Path | None = None
        original: bytes | None = None
        try:
            target_path, original = _apply(case)
        except Exception as exc:  # noqa: BLE001 - 脚本自身错误需要原样报出
            errors.append(case.id)
            print(f"   [错误] 施加变异失败：{exc}")
            continue

        try:
            code, out, cost = _run(case.argv, REPO / case.cwd, case.timeout)
        finally:
            restore_msg = _restore(case, target_path, original)
        if restore_msg != "sha 一致" and not restore_msg.startswith("已删除"):
            errors.append(case.id)
            print(f"   [错误] {restore_msg}")
            continue

        if code == -1:
            errors.append(case.id)
            print(f"   [错误] 判据超时（>{case.timeout}s）")
            print(_tail(out))
            continue

        if code != 0:
            caught.append(case.id)
            print(f"   [OK] 判据失败 exit={code} 用时 {cost:.1f}s｜基线:{baseline_ok}｜还原:{restore_msg}")
        else:
            missed.append(case.id)
            print(f"   [未抓] 判据竟然通过（exit 0, {cost:.1f}s）⇒ 该判据在空转")
            print(_tail(out))

    after = _git_status(targets)
    print("\n" + "=" * 78)
    print(f"被抓：{len(caught)}/{len(cases)}；未抓：{len(missed)}；错误：{len(errors)}")
    if missed:
        print(f"未抓列表：{missed}")
    if errors:
        print(f"错误列表：{errors}")
    if before != after:
        print("[文件完整性] 变异目标文件的 git 状态与开局不一致！")
        print(f"  开局：{before.splitlines()[:6]}")
        print(f"  结束：{after.splitlines()[:6]}")
        errors.append("GIT-DIRTY")
    else:
        print(f"[文件完整性] {len(targets)} 个变异目标文件的 git 状态与开局逐行一致（变异全部还原）")
    print("=" * 78)

    if errors:
        return 2
    return 1 if missed else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows GBK 控制台兜底
    except (AttributeError, OSError):  # pragma: no cover - 非 Windows / 老解释器
        pass
    raise SystemExit(main())
