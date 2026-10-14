# -*- coding: utf-8 -*-
"""R-9 二进制嗅探阈值 0.30 的标定（设计文档 §5.2.2）

用途：为 v1.6.0 桌面端「文本编辑器」的二进制嗅探阈值提供实测证据。
不修改任何项目源码；样本写在系统临时目录，结论写回本目录的 R9-结论.md。

运行：
    jflove-desktop/venv-win/Scripts/python.exe jflove-desktop/poc/editor-probes/r9-sniff-threshold.py

判定算法（A = 设计文档 §5.2.2 B5 原文口径）：
    non_text = 字节不在 {0x09,0x0A,0x0D} ∪ {0x20..0x7E}
               且 不是合法 UTF-8 多字节序列的一部分
    ratio    = non_text / n          （n = 前 8 KB 切片长度）

另实现两个对照算法，用于评估"0.30 不成立时"的替代判据：
    B = GB18030 感知：在 A 的基础上，把合法 GB18030 多字节序列的字节也算作文本
    C = 解码优先：先按 BOM/UTF-16 指纹，再 utf-8 strict 解码，再 gb18030 strict 解码
        （要求含多字节序列），全失败才判二进制；NUL 字节直接判二进制
"""

from __future__ import annotations

import json
import os
import random
import struct
import sys
import tempfile
from pathlib import Path

SNIFF_LIMIT = 8192  # 设计文档 §5.2.2：只嗅探前 8 KB
ASCII_TEXT = frozenset(list(range(0x20, 0x7F)) + [0x09, 0x0A, 0x0D])

OUT_DIR = Path(__file__).resolve().parent
WORK_DIR = Path(tempfile.gettempdir()) / "jflove-r9-probe"


# --------------------------------------------------------------------------
# 算法 A：设计原文口径
# --------------------------------------------------------------------------
def algorithm_a_non_text(sniff: bytes) -> int:
    """设计文档 §5.2.2 B5 的 non_text 口径：非 ASCII 可打印、且不属于合法 UTF-8 多字节序列的字节数。"""
    n = len(sniff)
    is_utf8_part = [False] * n
    i = 0
    while i < n:
        b = sniff[i]
        if b < 0x80:
            i += 1
            continue
        # 尝试按 UTF-8 前导字节解析一个多字节序列
        if 0xC2 <= b <= 0xDF:
            need = 1
        elif 0xE0 <= b <= 0xEF:
            need = 2
        elif 0xF0 <= b <= 0xF4:
            need = 3
        else:
            i += 1
            continue
        if i + need >= n + 1 and i + need > n:
            i += 1
            continue
        cont = sniff[i + 1 : i + 1 + need]
        if len(cont) == need and all(0x80 <= c <= 0xBF for c in cont):
            # 进一步校验过长编码（最小编码长度）以免把 C0/C1 当合法
            try:
                sniff[i : i + 1 + need].decode("utf-8")
            except UnicodeDecodeError:
                i += 1
                continue
            for k in range(i, i + 1 + need):
                is_utf8_part[k] = True
            i += 1 + need
        else:
            i += 1
    cnt = 0
    for idx, b in enumerate(sniff):
        if b in ASCII_TEXT:
            continue
        if is_utf8_part[idx]:
            continue
        cnt += 1
    return cnt


# --------------------------------------------------------------------------
# 算法 B：GB18030 感知（在 A 基础上追加 GB18030 多字节序列识别）
# --------------------------------------------------------------------------
def _gb18030_spans(data: bytes) -> list[tuple[int, int]]:
    """用标准库 codecs 增量解码，返回被 GB18030 多字节序列覆盖的字节区间；失败返回空表。"""
    spans: list[tuple[int, int]] = []
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b < 0x80:
            i += 1
            continue
        # 4 字节形式：0x81-0xFE, 0x30-0x39, 0x81-0xFE, 0x30-0x39
        if (
            i + 3 < n
            and 0x81 <= b <= 0xFE
            and 0x30 <= data[i + 1] <= 0x39
            and 0x81 <= data[i + 2] <= 0xFE
            and 0x30 <= data[i + 3] <= 0x39
        ):
            if data[i : i + 4].decode("gb18030", "strict") if False else True:
                spans.append((i, i + 4))
                i += 4
                continue
        # 2 字节形式
        if i + 1 < n and 0x81 <= b <= 0xFE:
            try:
                data[i : i + 2].decode("gb18030", "strict")
            except UnicodeDecodeError:
                pass
            else:
                spans.append((i, i + 2))
                i += 2
                continue
        i += 1
    return spans


def algorithm_b_non_text(sniff: bytes) -> int:
    a = algorithm_a_non_text(sniff)
    # A 已把合法 UTF-8 序列标记为文本；这里只追加 GB18030 覆盖但仍被判为 non_text 的字节
    n = len(sniff)
    utf8_part = [False] * n
    # 复用 A 的内部逻辑：重新计算一次文本字节集合
    text_mask = _text_mask_a(sniff)
    extra = 0
    for start, end in _gb18030_spans(sniff):
        for k in range(start, end):
            if not text_mask[k]:
                text_mask[k] = True
                extra += 1
    return a - extra


def _text_mask_a(sniff: bytes) -> list[bool]:
    """返回与 algorithm_a 一致的"文本字节"掩码。"""
    n = len(sniff)
    mask = [True] * n  # True = 文本
    is_utf8_part = [False] * n
    i = 0
    while i < n:
        b = sniff[i]
        if b < 0x80:
            i += 1
            continue
        if 0xC2 <= b <= 0xDF:
            need = 1
        elif 0xE0 <= b <= 0xEF:
            need = 2
        elif 0xF0 <= b <= 0xF4:
            need = 3
        else:
            i += 1
            continue
        cont = sniff[i + 1 : i + 1 + need]
        if len(cont) == need and all(0x80 <= c <= 0xBF for c in cont):
            try:
                sniff[i : i + 1 + need].decode("utf-8")
            except UnicodeDecodeError:
                i += 1
                continue
            for k in range(i, i + 1 + need):
                is_utf8_part[k] = True
            i += 1 + need
        else:
            i += 1
    for idx, b in enumerate(sniff):
        if b in ASCII_TEXT or is_utf8_part[idx]:
            mask[idx] = True
        else:
            mask[idx] = False
    return mask


# --------------------------------------------------------------------------
# 算法 C：解码优先（BOM / UTF-16 指纹 + utf-8 strict + gb18030 strict）
# --------------------------------------------------------------------------
def algorithm_c_decision(sniff: bytes) -> tuple[str, str]:
    """返回 (判定, 理由)。判定 ∈ {'text','binary'}。"""
    n = len(sniff)
    if n == 0:
        return "text", "n=0 empty"
    if sniff[:2] in (b"\xff\xfe", b"\xfe\xff") or sniff[:4] in (
        b"\xff\xfe\x00\x00",
        b"\x00\x00\xfe\xff",
    ):
        return "text", "BOM 显式声明文本"
    if b"\x00" in sniff:
        # 排除无 BOM 的 UTF-16（交替 NUL）
        even = sniff[0::2]
        odd = sniff[1::2]
        if n >= 16:
            er = even.count(0) / len(even) if even else 0
            orr = odd.count(0) / len(odd) if odd else 0
            if (er >= 0.9) != (orr >= 0.9):
                return "text", "交替 NUL 指纹 = 无 BOM UTF-16"
        return "binary", "含 NUL 字节"
    try:
        sniff.decode("utf-8", "strict")
        return "text", "utf-8 strict 解码成功"
    except UnicodeDecodeError:
        pass
    try:
        sniff.decode("gb18030", "strict")
    except UnicodeDecodeError:
        return "binary", "utf-8 / gb18030 strict 均失败"
    if any(0x81 <= b <= 0xFE for b in sniff):
        return "text", "gb18030 strict 解码成功且含多字节序列"
    return "binary", "gb18030 解码成功但无多字节序列（疑似 Latin-1 二进制）"


# --------------------------------------------------------------------------
# 设计文档 §5.2.2 的完整 B1~B6 流水线（用于"按 0.30 判定结果"一栏）
# --------------------------------------------------------------------------
def pipeline_b1_b6(sniff: bytes) -> tuple[str, str]:
    n = len(sniff)
    if n == 0:
        return "empty", "B1"
    if n >= 2 and sniff[0:2] in (b"\xff\xfe", b"\xfe\xff") or (
        n >= 4 and sniff[0:4] in (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")
    ):
        return "text", "B2 UTF-16/32 BOM"
    if b"\x00" in sniff and n >= 16:
        even = sniff[0::2]
        odd = sniff[1::2]
        er = even.count(0) / len(even) if even else 0
        orr = odd.count(0) / len(odd) if odd else 0
        if (er >= 0.9) != (orr >= 0.9):
            return "text", "B3 交替 NUL"
    if b"\x00" in sniff:
        return "binary", "B4 含 NUL"
    ratio = algorithm_a_non_text(sniff) / n
    if ratio > 0.30:
        return "binary", f"B5 ratio={ratio:.4f} > 0.30"
    return "text", f"B6 ratio={ratio:.4f} <= 0.30"


# --------------------------------------------------------------------------
# 样本构造
# --------------------------------------------------------------------------
def build_samples() -> list[dict]:
    random.seed(20261012)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    samples: list[dict] = []

    def add(key, title, data: bytes, path: Path, expect: str, note: str = ""):
        samples.append(
            {
                "key": key,
                "title": title,
                "path": str(path),
                "size": len(data),
                "data": data,
                "expect": expect,
                "note": note,
            }
        )

    # 1 纯 ASCII 文本
    lines = [
        f"2026-10-12 08:00:{i % 60:02d} INFO service=editor msg=heartbeat seq={i}"
        for i in range(700)
    ]
    ascii_text = ("\n".join(lines) + "\n").encode("ascii")
    p = WORK_DIR / "01-ascii.log"
    p.write_bytes(ascii_text)
    add("01", "纯 ASCII 文本（英文日志）", ascii_text, p, "text")

    # 2 UTF-8 中文
    han = "文本编辑器需要在打开文件之前判断它是不是二进制文件，绝对不能把中文当成二进制拒绝掉。"
    utf8_text = ("".join(f"{i:04d} {han}\n" for i in range(400))).encode("utf-8")
    p = WORK_DIR / "02-utf8-zh.txt"
    p.write_bytes(utf8_text)
    add("02", "UTF-8 中文（大量汉字 + 标点）", utf8_text, p, "text")

    # 3 GBK / GB18030 中文
    gbk_text = ("".join(f"{i:04d} {han}\n" for i in range(400))).encode("gb18030")
    p = WORK_DIR / "03-gbk-zh.txt"
    p.write_bytes(gbk_text)
    add("03", "GBK/GB18030 中文（.encode('gb18030')）", gbk_text, p, "text")

    # 4 UTF-16 LE 带 BOM 中文
    u16 = b"\xff\xfe" + ("".join(f"{i:04d} {han}\n" for i in range(400))).encode("utf-16-le")
    p = WORK_DIR / "04-utf16le-bom.txt"
    p.write_bytes(u16)
    add("04", "UTF-16 LE 带 BOM 中文", u16, p, "text")

    # 5 UTF-8 + BOM（纯 ASCII 正文外再加中文，避免与 02 重复）
    u8bom = b"\xef\xbb\xbf" + utf8_text
    p = WORK_DIR / "05-utf8-bom.txt"
    p.write_bytes(u8bom)
    add("05", "UTF-8 + BOM", u8bom, p, "text")

    # 6 真二进制：优先取系统 PE / PNG；取不到就用合成随机二进制
    real_bin = None
    real_src = ""
    for cand in [
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "notepad.exe",
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "user32.dll",
        Path(r"C:\Windows\System32\notepad.exe"),
    ]:
        if cand.is_file():
            real_bin = cand.read_bytes()[: 64 * 1024]
            real_src = str(cand)
            break
    if real_bin is None:
        real_bin = struct.pack("<4sI", b"\x7fELF", 0x01020304) + bytes(
            random.randrange(256) for _ in range(64 * 1024)
        )
        real_src = "合成（无系统二进制可用）"
    p = WORK_DIR / "06-real-binary.bin"
    p.write_bytes(real_bin)
    add("06", f"真二进制（{real_src}）", real_bin, p, "binary")

    # 7 二进制改名成 .log（白名单命中 ⇒ 设计上跳过嗅探）
    p = WORK_DIR / "07-renamed.log"
    p.write_bytes(real_bin)
    add(
        "07",
        "二进制改名成 .log（白名单命中，设计上跳过嗅探）",
        real_bin,
        p,
        "text(设计口径)",
        "§5.2.2 残余风险：白名单命中跳过嗅探，本条是需求已认可的有意取舍",
    )

    # 8 文本 + 二进制混合：前 4 KB ASCII，后接二进制
    mixed = ascii_text[:4096] + real_bin[: 64 * 1024 - 4096]
    p = WORK_DIR / "08-mixed.log"
    p.write_bytes(mixed)
    add("08", "文本与二进制混合（前 4 KB ASCII + 二进制）", mixed, p, "binary")

    # 补充：PNG 真二进制（若仓库内有可用的 png）
    png = None
    repo = Path(__file__).resolve().parents[3]
    for cand in repo.rglob("*.png"):
        try:
            png = cand.read_bytes()[: 64 * 1024]
        except OSError:
            continue
        png_src = str(cand)
        if len(png) > 2000:
            break
    if png is not None:
        p = WORK_DIR / "09-real-binary.png"
        p.write_bytes(png)
        add("09", f"真二进制 PNG（{png_src}）", png, p, "binary")

    # 补充：GBK 中文（较小样本，前 8 KB 全是中文正文）
    mixed_gbk = ("".join(f"{i:04d} {han}\n" for i in range(120))).encode("gb18030")
    p = WORK_DIR / "10-gbk-mixed.txt"
    p.write_bytes(mixed_gbk)
    add("10", "GBK 中文（较小样本，前 8 KB 全是中文正文）", mixed_gbk, p, "text")

    # 11 最坏情形：纯 GBK 汉字、零 ASCII 正文（GB2312 一级字区，两个字节都 ≥ 0xA1）
    pure_gbk = (han * 400).encode("gb18030")
    p = WORK_DIR / "11-gbk-pure.txt"
    p.write_bytes(pure_gbk)
    add("11", "纯 GBK 汉字（零 ASCII 正文，误杀最坏情形）", pure_gbk, p, "text")

    # 12 窗口边界泄漏：前 8 KB 全 ASCII，第 8 KB 之后才是二进制
    leak = ascii_text[:8192] + real_bin[: 64 * 1024 - 8192]
    p = WORK_DIR / "12-window-leak.log"
    p.write_bytes(leak)
    add(
        "12",
        "窗口边界泄漏：前 8 KB 全 ASCII，第 8192 字节后才是二进制",
        leak,
        p,
        "text(前8KB判据)",
        "「只嗅探前 8 KB」窗口的固有边界，非阈值问题",
    )

    # 13 无 NUL 随机二进制（0x01-0xFF 均匀，刻意避开 NUL 以免被 B4 直接拦下）
    #    这是"替代判据 C（解码优先）会不会误放"的关键对抗样本
    rnd = bytes(random.randrange(1, 256) for _ in range(64 * 1024))
    p = WORK_DIR / "13-random-no-nul.bin"
    p.write_bytes(rnd)
    add("13", "无 NUL 随机二进制（0x01-0xFF 均匀）", rnd, p, "binary")

    # 14 UTF-16 LE **无 BOM**：验证 B3（交替 NUL 指纹）是否真的能救回来
    u16nb = ("".join(f"{i:04d} {han}\n" for i in range(400))).encode("utf-16-le")
    p = WORK_DIR / "14-utf16le-nobom.txt"
    p.write_bytes(u16nb)
    add("14", "UTF-16 LE 无 BOM 中文（验证 B3 交替 NUL 指纹）", u16nb, p, "text")

    # 15 真 JPEG（前 8 KB 往往无 NUL，比 PE/PNG 更接近"漏放"风险）
    jpg_paths = [
        repo / "jflove-desktop" / "venv-win" / "Lib" / "site-packages" / "PySide6"
        / "scripts" / "deploy_lib" / "pyside_icon.jpg",
        repo / "jflove-web" / "node_modules" / "highlight.js" / "styles" / "pojoaque.jpg",
    ]
    for cand in jpg_paths:
        if cand.is_file():
            jpg = cand.read_bytes()[: 64 * 1024]
            p = WORK_DIR / "15-real-binary.jpg"
            p.write_bytes(jpg)
            add("15", f"真二进制 JPEG（{cand.name}）", jpg, p, "binary")
            break

    return samples


def risk_sweep(trials: int = 2000, window: int = 8192) -> dict:
    """替代判据的误放率：随机二进制（刻意不含 NUL）被误判为文本的比例。"""
    a_false, c_false, b_false = 0, 0, 0
    for _ in range(trials):
        data = bytes(random.randrange(1, 256) for _ in range(window))
        if algorithm_a_non_text(data) / window <= 0.30:
            a_false += 1
        if algorithm_b_non_text(data) / window <= 0.30:
            b_false += 1
        if algorithm_c_decision(data)[0] == "text":
            c_false += 1
    return {
        "trials": trials,
        "window": window,
        "a_false_accept": a_false,
        "b_false_accept": b_false,
        "c_false_accept": c_false,
        "a_false_rate": a_false / trials,
        "b_false_rate": b_false / trials,
        "c_false_rate": c_false / trials,
    }


def main() -> int:
    samples = build_samples()
    rows = []
    print(f"# R-9 二进制嗅探阈值实测（sniff = 前 {SNIFF_LIMIT} 字节）\n")
    header = (
        f"{'类别':<6}{'样本':<34}{'文件大小':>10}{'8KB内n':>8}"
        f"{'A非文本':>8}{'A比例':>9}{'B比例':>9}"
        f"  {'B1-B6判定':<26}{'A(0.30)':<10}{'B':<8}{'C':<8}{'期望'}"
    )
    print(header)
    print("-" * len(header))
    for s in samples:
        data = s["data"]
        sniff = data[:SNIFF_LIMIT]
        n = len(sniff)
        a_cnt = algorithm_a_non_text(sniff)
        b_cnt = algorithm_b_non_text(sniff)
        a_ratio = a_cnt / n if n else 0.0
        b_ratio = b_cnt / n if n else 0.0
        pipe, pipe_reason = pipeline_b1_b6(sniff)
        a_dec = "binary" if a_ratio > 0.30 else "text"
        b_dec = "binary" if b_ratio > 0.30 else "text"
        c_dec, c_reason = algorithm_c_decision(sniff)
        rows.append(
            {
                "key": s["key"],
                "title": s["title"],
                "path": s["path"],
                "file_size": s["size"],
                "sniff_len": n,
                "algo_a_non_text": a_cnt,
                "algo_a_ratio": round(a_ratio, 6),
                "algo_b_non_text": b_cnt,
                "algo_b_ratio": round(b_ratio, 6),
                "pipeline_b1_b6": pipe,
                "pipeline_reason": pipe_reason,
                "a_decision_030": a_dec,
                "b_decision_030": b_dec,
                "c_decision": c_dec,
                "c_reason": c_reason,
                "expect": s["expect"],
                "note": s["note"],
            }
        )
        print(
            f"{s['key']:<6}{s['title'][:32]:<34}{s['size']:>10}{n:>8}"
            f"{a_cnt:>8}{a_ratio:>9.4f}{b_ratio:>9.4f}"
            f"  {pipe + ' / ' + pipe_reason:<26}{a_dec:<10}{b_dec:<8}{c_dec:<8}{s['expect']}"
        )

    out_json = OUT_DIR / "r9-results.json"
    out_json.write_text(
        json.dumps({"sniff_limit": SNIFF_LIMIT, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[已写出] {out_json}")
    print(f"[样本目录] {WORK_DIR}")

    # 控制台再给一份 markdown 表，便于直接抄进结论
    md = [
        "| 类别 | 样本 | 文件大小(B) | 前8KB n | A 非文本字节 | A 比例 | B 比例 | B1~B6 判定 | 按 0.30 判定(A) | 期望 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        md.append(
            f"| {r['key']} | {r['title']} | {r['file_size']} | {r['sniff_len']} | "
            f"{r['algo_a_non_text']} | {r['algo_a_ratio']:.4f} | {r['algo_b_ratio']:.4f} | "
            f"{r['pipeline_b1_b6']}（{r['pipeline_reason']}） | {r['a_decision_030']} | {r['expect']} |"
        )
    table = "\n".join(md)

    # 判定汇总（以"设计口径 A + 完整 B1~B6 流水线"为准）
    ok, bad = [], []
    for r in rows:
        want = "text" if r["expect"].startswith("text") else "binary"
        got = "text" if r["pipeline_b1_b6"] in ("text", "empty") else "binary"
        (ok if want == got else bad).append(r["key"])
    verdict = [
        f"- 通过（判定与期望一致）：{', '.join(ok) if ok else '无'}",
        f"- 失败（判定与期望不符）：{', '.join(bad) if bad else '无'}",
    ]

    report = [f"# R-9 实测原始输出（sniff = 前 {SNIFF_LIMIT} 字节）", "", table, "", "## 判定汇总", *verdict, ""]
    (OUT_DIR / "r9-report.md").write_text("\n".join(report), encoding="utf-8")
    print(table)
    print("\n".join(verdict))

    # 误放率扫描：替代判据 vs 原判据（对"无 NUL 随机二进制"）
    sweep = risk_sweep()
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    payload["risk_sweep"] = sweep
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    sweep_md = [
        "",
        "## 误放率扫描（无 NUL 随机二进制，逐字节 0x01-0xFF 均匀）",
        "",
        f"- 试验次数：{sweep['trials']}，窗口：{sweep['window']} 字节（每次重新生成随机样本）",
        f"- 算法 A（设计原文口径，阈值 0.30）误放：{sweep['a_false_accept']} / {sweep['trials']}"
        f" = {sweep['a_false_rate']:.4f}",
        f"- 算法 B（GB18030 感知，阈值 0.30）误放：{sweep['b_false_accept']} / {sweep['trials']}"
        f" = {sweep['b_false_rate']:.4f}",
        f"- 算法 C（解码优先，无比例阈值）误放：{sweep['c_false_accept']} / {sweep['trials']}"
        f" = {sweep['c_false_rate']:.4f}",
        "",
    ]
    print("\n".join(sweep_md))
    with (OUT_DIR / "r9-report.md").open("a", encoding="utf-8") as fh:
        fh.write("\n".join(sweep_md))
    return 0


if __name__ == "__main__":
    sys.exit(main())
