# -*- coding: utf-8 -*-
"""R-6 补充实测（五）：用需求文档 AC-33 给定的标准判据复核输出

AC-33 原文判据：
  压缩后可用标准 XML 解析器解析成功（判据：python -c "import xml.etree.ElementTree as ET;
  ET.fromstring(open('out.xml',encoding='utf-8').read())" 退出码 0）且**文本节点内容不变**。

本脚本对 r6-out/ 下由 Node 侧探针产出的每个 XML 输出文件跑一遍该判据，并把
"输入 → 输出"的文本节点值逐一比对（ET 的 itertext 口径）。

运行：jflove-desktop/venv-win/Scripts/python.exe jflove-desktop/poc/editor-probes/r6-xml-ac33-check.py
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "r6-out"

# 与 Node 侧探针一致的输入（手工内联，保证本脚本可独立复跑）
INPUTS = {
    "ac33": '<root><!--c--><a><![CDATA[ x < y ]]></a><b>hello   world</b></root>',
    "ac33_space": '<root><!--c--><a><![CDATA[ x < y ]]></a><sp> </sp><b>hello   world</b></root>',
    "pretty": "<root>\n  <a>1</a>\n  <b>2</b>\n</root>",
}


def texts(xml: str):
    """返回文档中全部文本节点值（含 CDATA 展开后的文本），口径 = ET.itertext()"""
    root = ET.fromstring(xml)
    return [t for t in root.itertext()]


def check(name: str, path: Path, expect_input: str):
    raw = path.read_text(encoding="utf-8")
    row = {"name": name, "file": path.name}
    try:
        ET.fromstring(raw)  # AC-33 的退出码判据
    except ET.ParseError as exc:
        row["parse_error"] = f"{type(exc).__name__}: {exc}"
        row["parse_ok"] = False
        return row
    row["parse_ok"] = True
    try:
        row["input_texts"] = texts(expect_input)
        row["output_texts"] = texts(raw)
        row["texts_equal"] = row["input_texts"] == row["output_texts"]
    except ET.ParseError as exc:
        row["input_parse_error"] = str(exc)
    # 注释与 CDATA 的形态（字符级）
    row["has_comment"] = "<!--c-->" in raw or "<!--keep-me-->" in raw or "<!--" in raw
    row["has_cdata_literal"] = "<![CDATA[" in raw
    return row


def main() -> int:
    rows = []
    targets = [
        ("AC-33 原样样例 · 推荐参数格式化", "a8-ac33-plain.safeformat.out.xml", INPUTS["ac33"]),
        ("AC-33 原样样例 · 推荐参数压缩", "a8-ac33-plain.safemin.out.xml", INPUTS["ac33"]),
        ("AC-33 + <sp> </sp> · 压缩被拒绝（无产物）", None, INPUTS["ac33_space"]),
        ("AC-33 原文样例 · 库默认格式化（未包装）", "x2b-ac33-sample.out.xml", INPUTS["ac33"]),
        ("AC-33 原文样例 · 库默认压缩（未包装）", "x6e-minify-ac33.out.xml", INPUTS["ac33"]),
        ("含 <a> </a> · 库默认压缩（未包装，已知会坏）", "x6b-minify-space-textnode.out.xml", "<r>\n  <a> </a>\n  <b>x</b>\n</r>"),
        ("含 <a> </a> · 库默认格式化（未包装，已知会坏）", "x3b-empty-tag.out.xml", "<r><a></a><b> </b><c/></r>"),
        ("正常缩进 · 推荐参数压缩", "a2-pretty.safemin.out.xml", INPUTS["pretty"]),
        ("正常缩进 · 推荐参数格式化", "a2-pretty.safeformat.out.xml", INPUTS["pretty"]),
        ("xml:space=preserve · 推荐参数压缩", "a5-xmlspace.safemin.out.xml", '<root xml:space="preserve"><a> </a></root>'),
        ("xml:space=preserve · 推荐参数格式化", "a5-xmlspace.safeformat.out.xml", '<root xml:space="preserve"><a> </a></root>'),
    ]
    for name, fname, inp in targets:
        if fname is None:
            rows.append({"name": name, "file": None, "skipped": "该用例被安全包装拒绝，无产物（符合预期）"})
            continue
        p = OUT_DIR / fname
        if not p.is_file():
            rows.append({"name": name, "file": fname, "missing": True})
            continue
        rows.append(check(name, p, inp))

    print(f"{'用例':<44}{'解析':<8}{'文本节点不变':<14}{'注释':<8}{'CDATA':<8}")
    print("-" * 88)
    for r in rows:
        if r.get("skipped"):
            print(f"{r['name']:<44}{'—':<8}{'—':<14}{'—':<8}{'—':<8}  ({r['skipped']})")
            continue
        if r.get("missing"):
            print(f"{r['name']:<44}{'文件缺失':<8}")
            continue
        ok = "OK" if r.get("parse_ok") else "FAIL"
        te = r.get("texts_equal")
        te_s = "是" if te is True else ("否" if te is False else "—")
        print(
            f"{r['name']:<44}{ok:<8}{te_s:<14}"
            f"{'有' if r.get('has_comment') else '无':<8}{'有' if r.get('has_cdata_literal') else '无':<8}"
        )
        if te is False:
            print(f"    input_texts  = {r.get('input_texts')}")
            print(f"    output_texts = {r.get('output_texts')}")

    (HERE / "r6-ac33-check.json").write_text(
        json.dumps({"rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n已写出：{HERE / 'r6-ac33-check.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
