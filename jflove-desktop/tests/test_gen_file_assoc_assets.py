"""
打包资产派生脚本（`scripts/gen_file_assoc_assets.py`）单元测试

设计依据：`文档记录/设计文档/v1.6.0.md`
  - §5.1：单一真相 = `scratch-types.ts` 的 `TYPES`；`.iss` / `.desktop` / `.xml` / Python
    **都不得**手打扩展名清单；一致性判据 `--check` 退出码 0/1（含**反向验证**）；
  - §6.7.2：Windows 只写 `HKCU\\Software\\Classes\\Applications\\JFLove.exe`
    （`SupportedTypes` 逐扩展名空值 + `shell\\open\\command` + `DefaultIcon`），
    **不写** `.ext` 类键 / `UserChoice` / `ChangesAssociations`；
  - §6.7.3：Linux 侧 `jflove.xml`（`application/x-jflove-text` + 逐扩展名 glob）与
    `.desktop` 的 `MimeType=` 行。

覆盖点：
  1. 生成 → `--check` 通过（exit 0）；手改 manifest / 三处生成物任一 ⇒ exit 1 且带 diff
  2. `.desktop` 的 `MimeType=` 块**幂等重写**：只动标记块内那一行，其余逐行不变
  3. `jflove.xml` 含 `application/x-jflove-text` 且每条扩展名一个 glob
  4. `file-assoc.iss` 的注册行形态正确（HKCU / SupportedTypes 空值 / command / DefaultIcon），
     且**有效行**里没有 `.ext` 键、`UserChoice`、`ChangesAssociations`
  5. manifest 缺失 / 结构非法 ⇒ exit 1（响亮失败，不产出半成品）
  6. 真实派生链（TS → manifest → 资产）端到端：有 node 时用真实 `scratch-types.ts` 走一遍，
     再往 manifest 里加 `.zzz` ⇒ 派生核对必须红

安全口径：全部夹具写在 `tmp_path` 下（**只读**真实仓库里的 `scratch-types.ts`），
绝不修改仓库内的打包资产。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_DESKTOP_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _DESKTOP_ROOT / "scripts" / "gen_file_assoc_assets.py"
_MJS = _DESKTOP_ROOT / "scripts" / "gen_file_assoc_manifest.mjs"


def _load_assets_module():
    """把生成脚本作为模块加载（它不是包，无法直接 import）。"""
    spec = importlib.util.spec_from_file_location("gen_file_assoc_assets", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


assets = _load_assets_module()

#: 夹具 manifest（字段形状照 §5.1）
MANIFEST = {
    "version": 1,
    "generatedFrom": "config/scratch-types.ts",
    "types": [
        {"ext": "json", "label": "JSON", "language": "json", "mime": ["application/json"]},
        {"ext": "md", "label": "Markdown", "language": "markdown", "mime": ["text/markdown"]},
        {"ext": "log", "label": "日志 (log)", "language": "text", "mime": ["text/plain"]},
        {"ext": "patch", "label": "Patch", "language": "diff", "mime": ["text/x-diff"]},
    ],
}
MANIFEST_EXTS = ["json", "md", "log", "patch"]

#: 夹具 `.desktop`（与真实文件同构：有 `Icon=` 锚点 + 无标记块）
DESKTOP_FIXTURE = (
    "[Desktop Entry]\n"
    "Type=Application\n"
    "Version=1.0\n"
    "Name=JFLove\n"
    "Exec=jflove\n"
    "Icon=jflove\n"
    "Terminal=false\n"
    "Categories=Network;FileTransfer;\n"
    "StartupWMClass=JFLove\n"
)


def _make_root(tmp_path: Path, desktop_text: str | None = None) -> Path:
    """造一个"jflove-desktop 根"夹具（manifest + .desktop），其余生成物由脚本产出。"""
    root = tmp_path / "desktop"
    (root / "ui" / "public").mkdir(parents=True)
    (root / "packaging" / "linux").mkdir(parents=True)
    (root / "packaging" / "windows").mkdir(parents=True)
    (root / "ui" / "public" / assets.MANIFEST_NAME).write_text(
        json.dumps(MANIFEST, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (root / "packaging" / "linux" / "jflove.desktop").write_text(
        DESKTOP_FIXTURE if desktop_text is None else desktop_text, encoding="utf-8"
    )
    return root


def _generate(root: Path) -> int:
    """无参/`--write` 生成模式（跳过 Node 派生核对：夹具没有 scratch-types.ts）。"""
    return assets.main(["--root", str(root), "--write"])


def _check(root: Path, *extra: str) -> int:
    """`--check` 模式（夹具默认跳过 Node 派生核对）。"""
    return assets.main(["--root", str(root), "--check", "--no-derive-check", *extra])


def _active_lines(text: str) -> str:
    """取出 `.iss` 的**有效行**（去注释、去空行）。

    生成物头部有意用注释记录了"禁止写什么"（含 `UserChoice` 等反例文本），
    朴素的全文本 `in` 判断会被注释误伤 —— 与 `test_build_installer_isl.py` 同一手法。
    """
    kept = [line for line in text.splitlines()
            if line.strip() and not line.strip().startswith(";")]
    return "\n".join(kept)


# ── 1. 生成 + --check 正反两向 ───────────────────────────────────────

def test_write_then_check_passes(tmp_path, capsys):
    root = _make_root(tmp_path)
    assert _generate(root) == 0
    assert (root / "packaging" / "linux" / "jflove.xml").is_file()
    assert (root / "packaging" / "windows" / "file-assoc.iss").is_file()
    assert _check(root) == 0
    assert "一致" in capsys.readouterr().out


def test_write_is_idempotent(tmp_path):
    """第二次生成不改变任何字节（生成物可复现）。"""
    root = _make_root(tmp_path)
    assert _generate(root) == 0
    targets = [root / "packaging" / "linux" / "jflove.xml",
               root / "packaging" / "linux" / "jflove.desktop",
               root / "packaging" / "windows" / "file-assoc.iss"]
    before = [p.read_bytes() for p in targets]
    assert _generate(root) == 0
    assert [p.read_bytes() for p in targets] == before


def test_check_detects_manifest_with_extra_ext(tmp_path, capsys):
    """**反向验证**：往 manifest 里加一项 `.zzz` ⇒ `--check` 必须 exit 1 并打印 diff。"""
    root = _make_root(tmp_path)
    assert _generate(root) == 0
    manifest_path = root / "ui" / "public" / assets.MANIFEST_NAME
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["types"].append({"ext": "zzz", "label": "ZZZ", "language": "text",
                             "mime": ["text/plain"]})
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")

    assert _check(root) == 1
    captured = capsys.readouterr()
    assert "zzz" in captured.err
    assert "不一致" in captured.err


def _tamper(path: Path, target: str) -> None:
    """按生成物的"归属范围"制造篡改：

    - `jflove.xml` / `file-assoc.iss` 是**整文件**生成物 ⇒ 追加一行即可被查出；
    - `jflove.desktop` **只有标记块**归生成器所有 ⇒ 必须改块内的 `MimeType=` 行才算篡改
      （块外的行本来就该由人维护，改它不该报错 —— 这条边界正是幂等重写的前提）。
    """
    if target == "jflove.desktop":
        lines = path.read_text(encoding="utf-8").splitlines()
        tampered = ["MimeType=application/x-tampered;" if line.startswith("MimeType=") else line
                    for line in lines]
        path.write_text("\n".join(tampered) + "\n", encoding="utf-8")
        return
    path.write_text(path.read_text(encoding="utf-8") + "; 手工改动\n", encoding="utf-8")


@pytest.mark.parametrize("target", ["jflove.xml", "jflove.desktop", "file-assoc.iss"])
def test_check_detects_tampered_asset(tmp_path, target, capsys):
    """三处生成物任一被手改 ⇒ exit 1（生成物不是真相，手改必红）。"""
    root = _make_root(tmp_path)
    assert _generate(root) == 0
    path = (root / "packaging" / "linux" / target if target != "file-assoc.iss"
            else root / "packaging" / "windows" / target)
    _tamper(path, target)

    assert _check(root) == 1
    assert "不一致" in capsys.readouterr().err


def test_check_tolerates_hand_edits_outside_desktop_block(tmp_path):
    """`.desktop` 块外的行属于人工维护：改它**不该**让 --check 变红（幂等重写的边界）。"""
    root = _make_root(tmp_path)
    assert _generate(root) == 0
    desktop = root / "packaging" / "linux" / "jflove.desktop"
    desktop.write_text(
        desktop.read_text(encoding="utf-8").replace("Categories=Network;FileTransfer;",
                                                    "Categories=Utility;TextEditor;"),
        encoding="utf-8",
    )
    assert _check(root) == 0


def test_check_reports_missing_generated_file(tmp_path, capsys):
    root = _make_root(tmp_path)
    assert _generate(root) == 0
    (root / "packaging" / "linux" / "jflove.xml").unlink()
    assert _check(root) == 1
    assert "缺少生成物" in capsys.readouterr().err


def test_missing_manifest_fails_loudly(tmp_path, capsys):
    root = _make_root(tmp_path)
    (root / "ui" / "public" / assets.MANIFEST_NAME).unlink()
    assert _generate(root) == 1
    assert "找不到 manifest" in capsys.readouterr().err


@pytest.mark.parametrize("payload", [
    {"version": 2, "types": MANIFEST["types"]},
    {"version": 1, "types": []},
    {"version": 1, "types": [{"ext": ".json", "mime": ["application/json"]}]},
    {"version": 1, "types": [{"ext": "json", "mime": []}]},
    {"version": 1, "types": [{"ext": "json", "mime": ["application/json"]},
                             {"ext": "json", "mime": ["application/json"]}]},
])
def test_malformed_manifest_fails_loudly(tmp_path, payload, capsys):
    """manifest 结构非法（版本 / 空表 / 带点 ext / 空 mime / 重复）⇒ exit 1。"""
    root = _make_root(tmp_path)
    (root / "ui" / "public" / assets.MANIFEST_NAME).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    assert _generate(root) == 1
    assert "失败" in capsys.readouterr().err


# ── 2. .desktop 幂等重写 ─────────────────────────────────────────────

def test_desktop_block_is_inserted_after_icon(tmp_path):
    root = _make_root(tmp_path)
    assert _generate(root) == 0
    lines = (root / "packaging" / "linux" / "jflove.desktop").read_text(
        encoding="utf-8").splitlines()
    begin = lines.index(assets.DESKTOP_MARK_BEGIN)
    assert lines[begin - 1] == "Icon=jflove"
    assert lines[begin + 2] == assets.DESKTOP_MARK_END
    mime_line = lines[begin + 1]
    assert mime_line.startswith("MimeType=")
    assert mime_line.endswith(";")
    assert "application/json;" in mime_line
    assert f"{assets.SELF_MIME_TYPE};" in mime_line


def test_desktop_rewrite_preserves_unrelated_lines(tmp_path):
    """除标记块外的行**逐行不变**（幂等重写，不重排文件）。"""
    root = _make_root(tmp_path)
    desktop = root / "packaging" / "linux" / "jflove.desktop"
    assert _generate(root) == 0
    first = desktop.read_text(encoding="utf-8")
    assert _generate(root) == 0
    assert desktop.read_text(encoding="utf-8") == first

    kept = [line for line in first.splitlines()
            if line.strip() != assets.DESKTOP_MARK_BEGIN
            and line.strip() != assets.DESKTOP_MARK_END
            and not line.startswith("MimeType=")]
    assert kept == DESKTOP_FIXTURE.splitlines()


def test_desktop_hand_edited_mime_line_is_rewritten(tmp_path):
    """标记块内被手改的 `MimeType=` ⇒ 下次生成原样纠正（标记块属于生成物）。"""
    root = _make_root(tmp_path)
    desktop = root / "packaging" / "linux" / "jflove.desktop"
    assert _generate(root) == 0
    lines = desktop.read_text(encoding="utf-8").splitlines()
    tampered = ["MimeType=application/x-tampered;" if line.startswith("MimeType=") else line
                for line in lines]
    desktop.write_text("\n".join(tampered) + "\n", encoding="utf-8")

    assert _generate(root) == 0
    text = desktop.read_text(encoding="utf-8")
    assert "application/x-tampered" not in text
    assert "application/json;" in text


def test_render_desktop_without_markers_appends_block():
    """无标记 + 无锚点的极端情况：块追加到末尾，原有内容一行不少。"""
    text = "[Desktop Entry]\nType=Application\nName=JFLove\n"
    rendered = assets.render_desktop(text, ["application/json", assets.SELF_MIME_TYPE])
    lines = rendered.splitlines()
    assert lines[:3] == text.splitlines()
    assert lines[3] == assets.DESKTOP_MARK_BEGIN
    assert lines[4] == "MimeType=application/json;application/x-jflove-text;"


def test_render_desktop_cleans_duplicated_markers():
    """标记重复/残缺（手工粘贴造成）⇒ 清干净后只留一对（仍然幂等）。"""
    text = (DESKTOP_FIXTURE
            + assets.DESKTOP_MARK_BEGIN + "\n"
            + "MimeType=application/old;\n"
            + assets.DESKTOP_MARK_END + "\n"
            + assets.DESKTOP_MARK_BEGIN + "\n"
            + "MimeType=application/older;\n"
            + assets.DESKTOP_MARK_END + "\n")
    rendered = assets.render_desktop(text, ["application/json"])
    assert rendered.count(assets.DESKTOP_MARK_BEGIN) == 1
    assert rendered.count(assets.DESKTOP_MARK_END) == 1
    assert "application/old" not in rendered and "application/older" not in rendered
    assert assets.render_desktop(rendered, ["application/json"]) == rendered


# ── 3. jflove.xml（§6.7.3） ──────────────────────────────────────────

def test_xml_declares_self_mime_and_one_glob_per_ext(tmp_path):
    root = _make_root(tmp_path)
    assert _generate(root) == 0
    xml = (root / "packaging" / "linux" / "jflove.xml").read_text(encoding="utf-8")
    assert f'<mime-type type="{assets.SELF_MIME_TYPE}">' in xml
    assert xml.count("<glob ") == len(MANIFEST_EXTS)
    for ext in MANIFEST_EXTS:
        assert f'<glob pattern="*.{ext}"/>' in xml
    assert "shared-mime-info" in xml


def test_xml_is_parseable(tmp_path):
    """产物必须是格式良好的 XML（shared-mime-info 会直接解析它）。"""
    import xml.etree.ElementTree as element_tree

    root = _make_root(tmp_path)
    assert _generate(root) == 0
    tree = element_tree.parse(root / "packaging" / "linux" / "jflove.xml")
    namespace = "{http://www.freedesktop.org/standards/shared-mime-info}"
    mime_types = tree.getroot().findall(f"{namespace}mime-type")
    assert len(mime_types) == 1
    assert mime_types[0].get("type") == assets.SELF_MIME_TYPE
    globs = mime_types[0].findall(f"{namespace}glob")
    assert sorted(node.get("pattern") for node in globs) == sorted(
        f"*.{ext}" for ext in MANIFEST_EXTS
    )


# ── 4. file-assoc.iss（§6.7.2） ──────────────────────────────────────

def test_iss_registers_supported_types_and_command(tmp_path):
    root = _make_root(tmp_path)
    assert _generate(root) == 0
    iss = (root / "packaging" / "windows" / "file-assoc.iss").read_text(encoding="utf-8")
    supported = f'Subkey: "{assets.APP_REG_PATH}\\SupportedTypes"'
    for ext in MANIFEST_EXTS:
        assert (f'Root: HKCU; {supported}; ValueType: string; '
                f'ValueName: ".{ext}"; ValueData: ""') in iss
    assert f'Subkey: "{assets.APP_REG_PATH}\\DefaultIcon"' in iss
    assert f'Subkey: "{assets.APP_REG_PATH}\\shell\\open\\command"' in iss
    assert 'ValueData: """{app}\\JFLove.exe"" ""%1"""' in iss
    assert f'#define FILE_ASSOC_EXTS "{",".join(MANIFEST_EXTS)}"' in iss
    assert f"#define FILE_ASSOC_COUNT {len(MANIFEST_EXTS)}" in iss


def test_iss_active_lines_never_hijack_default_program(tmp_path):
    """**有效行**里不得出现抢占默认程序的写法（D18 / AC-43 ②）。"""
    root = _make_root(tmp_path)
    assert _generate(root) == 0
    raw = (root / "packaging" / "windows" / "file-assoc.iss").read_text(encoding="utf-8")
    active = _active_lines(raw)

    assert "UserChoice" not in active
    assert "ChangesAssociations" not in active
    assert "HKLM" not in active
    assert 'Subkey: "Software\\Classes\\.' not in active      # 不得写 .ext 形态的键
    assert 'Subkey: "Software\\Classes\\Applications\\JFLove.exe' in active
    assert all(line.startswith("Root: HKCU;") for line in active.splitlines()
               if line.startswith("Root:"))


# ── 5. 真实派生链（TS → manifest → 资产）端到端 ──────────────────────

def _node() -> str:
    node = shutil.which("node") or shutil.which("node.exe")
    if node is None:
        pytest.skip("构建机没有 node：跳过真实派生链用例")
    return node


def test_full_derivation_chain_with_real_scratch_types(tmp_path):
    """用**真实** `scratch-types.ts` 走一遍：生成器 → manifest → 三处资产 → `--check` 0。

    再往 manifest 里加一项 ⇒ `--check` 必须 1（反向验证在真实真相上也成立）。
    """
    node = _node()
    root = _make_root(tmp_path)
    (root / "ui" / "src" / "config").mkdir(parents=True)
    ts_src = _DESKTOP_ROOT / "ui" / "src" / "config" / "scratch-types.ts"
    ts_dst = root / "ui" / "src" / "config" / "scratch-types.ts"
    shutil.copy2(ts_src, ts_dst)
    manifest_path = root / "ui" / "public" / assets.MANIFEST_NAME

    proc = subprocess.run(
        [node, str(_MJS), "--src", str(ts_dst), "--out", str(manifest_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, proc.stderr
    assert _generate(root) == 0
    assert assets.main(["--root", str(root), "--check"]) == 0     # 含 Node 派生核对

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["types"].append({"ext": "zzz", "label": "ZZZ", "language": "text",
                             "mime": ["text/plain"]})
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    assert assets.main(["--root", str(root), "--check"]) == 1


def test_cli_exit_codes(tmp_path):
    """命令行入口的退出码契约（子进程口径，与人类手敲完全一致）。"""
    root = _make_root(tmp_path)
    env_args = [sys.executable, str(_SCRIPT), "--root", str(root)]
    # 子进程的 stdio 编码随本机 locale；显式钉成 UTF-8，断言才与文案一致
    child_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    run_kwargs = {"capture_output": True, "text": True, "encoding": "utf-8",
                  "errors": "replace", "env": child_env}

    written = subprocess.run([*env_args, "--write"], **run_kwargs)
    assert written.returncode == 0, written.stderr

    checked = subprocess.run([*env_args, "--check", "--no-derive-check"], **run_kwargs)
    assert checked.returncode == 0, checked.stderr

    (root / "packaging" / "windows" / "file-assoc.iss").write_text("; broken\n",
                                                                   encoding="utf-8")
    broken = subprocess.run([*env_args, "--check", "--no-derive-check"], **run_kwargs)
    assert broken.returncode == 1
    assert "不一致" in broken.stderr
