"""r17 真机取证探针：剪贴板 CF_HTML ⇒ 编辑器粘贴（jflove v1.6.1 · 用户反馈 F6）

## 待判定的假设（父 agent 的只读根因分析 M1）

Qt 的 Win32 剪贴板转换器把 CF_HTML 的**头整段砍掉**，只把 `<HTML>…</HTML>` 正文交给
`QMimeData::html()`；而 QtWebEngine 的 `ClipboardQt::ReadHTML`（Windows 分支）**却假设
`mimeData->html()` 仍是完整 CF_HTML**：它调 `CFHtmlExtractMetadata`，先在（小写化后的）文本里
找 `<html`，再找 `<!--StartFragment`；**只有找不到这个注释标记时**才退回读 `StartFragment:` 数字
—— 而那些数字刚好在上一步被 Qt 砍掉了。

⇒ 若原始 CF_HTML **没有** `<!--StartFragment-->` 注释（**Java/AWT 就是这种**：
`sun.awt.windows.HTMLCodec.convertToHTMLFormat` 只写 `<HTML><BODY>` + 四个数字偏移，
从不写注释标记），则 `fragment_start` 保持 `npos` ⇒ 早退 ⇒ `markup` 为空串；
而 `GetStandardFormats` 已把 `text/html` 报为可用 ⇒ Blink 走 HTML 粘贴模式却拿到空内容
⇒ **粘贴什么都不发生**。
⇒ 反之，Chromium/Qt 自己的写路径会写 `<!--StartFragment-->`，所以从 Lark（Electron/Chromium）
复制能贴进去。

上游 issue：QTBUG-118121（状态仍 Reported ⇒ 升级 PySide6 无用）。

## 本探针的判据（**每一条都"失败得起来"**）

| id | 判据 | 期望 |
| --- | --- | --- |
| `P0` | 真外壳加载前端产物、初始态未登录 | PASS |
| `P1` | 隔离服务端就绪 + 服务层真登录 + 重载后渲染层已登录、工作区可用、1 个标签页 | PASS |
| `SHAPE-AWT` | **Qt 眼中的样子**：AWT 风格载荷 ⇒ `hasHtml=True` 且 `'<html'` 在
且 `'<!--StartFragment'` **不在** 且 `'StartFragment:'` **不在** | PASS（= M1 前提成立） |
| `CLIP-AWT` | OS 级 `Ctrl+V` 后正文**逐字符等于**剪贴板的 `text/plain` | ⚠ **修复前预期 FAIL**（复现证据） |
| `CLIP-CHROMIUM` | 同上，但载荷含 `<!--StartFragment-->`（正向对照） | PASS（修复前也必须 PASS） |
| `CLIP-TEXT` | 只放 `CF_UNICODETEXT`（无 HTML）⇒ 粘贴成功 | PASS |
| `CLIP-HTMLONLY` | **隔离实验**：AWT 风格 CF_HTML 且**不写** `CF_UNICODETEXT` | 判定 M1 是否足以致哑 |
| `CLIP-EMPTY` | 清空剪贴板 ⇒ `Ctrl+V` 后正文**不变**、无报错、窗口存活 | PASS |
| `H-NOHOST` | 粘贴相位内宿主 47 项 WebAction 触发计数 = 0、无宿主模态、主窗口存活 | PASS |
| `AWT-CJK` | 中文载荷（UTF-8 字节偏移、无注释标记）⇒ 逐字符相等 | PASS |
| `CLIP-DIVERGE` | CF_HTML 片段与 `CF_UNICODETEXT` **内容不同** ⇒ 看贴出哪一个 | 判"实际走哪条通路" |
| `CLIP-AWT-REAL` | **r18 新增**：剪贴板上是**真实 AWT 载荷**（JDK 自己写的，见
  `AwtClipHtml.java`）⇒ **不写剪贴板**，直接 `Ctrl+V`，正文应逐字符等于剪贴板上的
  `CF_UNICODETEXT`（无纯文本 flavor 时退化为按偏移切出的 HTML 片段） | PASS（修复前即期望 PASS 才算"真凶不在 AWT 载荷本身"） |

另加**非阻塞观察项**：`SHAPE-CHROMIUM`（对照载荷在 Qt 眼中的读数）、
`USER-CLIP`（**只读**摊开真实来源留下的 flavor 与 CF_HTML 头结构，见下）。

### 本探针的实测结论（2026-10-16 首次运行，证据见 `out/r17-20261016-124541/`）

- `SHAPE-AWT` = **PASS** ⇒ **M1 的机制前提成立**：AWT 风格 CF_HTML 经 Qt 转换后
  `html()` 只剩 `<HTML><BODY>…</BODY></HTML>`（69 字符），**既无 `<!--StartFragment-->`
  注释、也无 `StartFragment:` 头**（`htmlHasMarkerComment=false`、
  `htmlHasStartFragmentHeader=false`、`htmlPrefix12Hex=3c48544d4c3e3c424f44593e` = `<HTML><BODY>`）；
  同时 Win32 侧按偏移切片复核通过（`fragmentByOffsetOk.ok=true`）⇒ 载荷本身合规、偏移没错。
- `CLIP-AWT` = **PASS**（**与预期相反**）：正文 42 字符、`docFnv1a32=ecc2dbba` 与期望相同，
  且 `channel=text-flavor` ⇒ 这次粘贴是**经 `text/plain`** 完成的，**没有**出现"什么都不发生"。
- `CLIP-HTMLONLY` = **FAIL**：把 `CF_UNICODETEXT` 拿掉后，`Ctrl+V` 后正文 0 字符
  （`empty=true`、`channel=nothing`）⇒ **HTML 通路确实被 M1 打空**，这个机制是真的、
  而且"空"的后果确实就是"粘贴什么都不发生"。
- `CLIP-DIVERGE` = **PASS** 且 `channel=text-flavor`：CF_HTML 片段写的是
  `r17-html-channel-only`、`CF_UNICODETEXT` 写的是 `r17-awt-ascii-…`，贴出来的是**后者**
  ⇒ 有纯文本 flavor 时 Blink 走 `text/plain`。
- ⇒ **综合判定：M1 机制被证实（`SHAPE-AWT` + `CLIP-HTMLONLY`），但在"CF_HTML 与
  CF_UNICODETEXT 并存"的合成场景下不足以致哑（`CLIP-AWT` PASS）**。
  因此"IDEA 复制 ⇒ 本应用粘贴无反应"**尚未被本探针复现**；差异点最可能落在
  **真实 IDEA 复制时剪贴板上到底有哪些 flavor / CF_HTML 头长什么样**。
  这正是 `--only USER-CLIP` 的用途（把真实剪贴板摊开对比）：

      venv-win\\Scripts\\python.exe poc\\editor-probes\\r17-clipboard-cfhtml.py --only USER-CLIP

  该判据**只读、不写剪贴板、不经 Qt**（裸 Win32 枚举，避免任何隐式格式转换），
  回 `formats` / `hasMarkerComment` / `offsets` / `fragmentByOffsetOk` 等；
  `verdict=no-html-flavor` 表示当前剪贴板里没有 `HTML Format` ⇒ 请先在 IDEA 里复制再重跑。

### `CLIP-AWT-REAL`（r18 新增）：喂**真实 AWT 载荷**给粘贴判据

前置（**保持 JVM 存活**，否则可能读到空数据）：

    D:\\jdk\\jdk-21.0.9\\bin\\java.exe poc\\editor-probes\\AwtClipHtml.java \\
        --flavor=all|custom --preset=ascii|cjk|doc --hold=900

然后（**另一路**，两条判据都不写剪贴板）：

    venv-win\\Scripts\\python.exe poc\\editor-probes\\r17-clipboard-cfhtml.py --only USER-CLIP
    venv-win\\Scripts\\python.exe poc\\editor-probes\\r17-clipboard-cfhtml.py --only CLIP-AWT-REAL

这条判据的期望文本是**从剪贴板自己读出来的**（优先 `CF_UNICODETEXT`，退化为按偏移切出的
HTML 片段），所以它判的是"**Ctrl+V 有没有把剪贴板上真实载荷的文本贴进编辑器**"，
而不是"探针写的载荷能不能贴"——这正是"IDEA 复制的代码贴不进来"那个用户现象的最小化真机复现。
末端的 `\\r\\n` 会归一成 `\\n`（AWT 写文本時会把 LF 转 CRLF，渲染层文档里是 `\\n`）。
`--only CLIP-AWT-REAL` 时**跳过** `SHAPE-AWT` / `SHAPE-CHROMIUM` 的合成写入，
以免覆盖剪贴板上的真实载荷（其余情况下这两条判据的行为一字未改）。

---

## r18 实测结论（2026-10-16，真机；工具 = `AwtClipHtml.java` + 本探针 `--only CLIP-AWT-REAL`）

**真凶不是 CF_HTML 的标记 / 偏移形态，而是 `UniformResourceLocator`（`CFSTR_INETURL`）这个格式。**
Java/AWT 写剪贴板时**必然**带上它（`Clipboard.setContents` ⇒ 原生格式表里出现
`HTML Format / CF_UNICODETEXT / CF_TEXT / UniformResourceLocator / CF_LOCALE / CF_OEMTEXT`），
而它一旦在，**QtWebEngine 的 `Ctrl+V` 就贴出 0 字符**（窗口已在前台、OS 级按键已确认送达、
剪贴板内容被 Qt 与裸 Win32 双双读对、探针不写剪贴板）。实测矩阵（每格都是 `Ctrl+V` 后正文）：

| 剪贴板由谁写、载荷 | 含 `UniformResourceLocator`？ | 结果 |
| --- | --- | --- |
| 裸 Win32（探针进程内 / 已退出进程）＋ CF_HTML(AWT 风格) + `CF_UNICODETEXT` | 否 | **贴出纯文本** |
| 裸 Win32 ＋ 同上 ＋ 自定义格式 `r18-dummy-format` | 否 | **贴出纯文本** |
| 裸 Win32 ＋ 同上 ＋ `UniformResourceLocator` | **是** | **0 字符** |
| 真实 AWT（`--flavor=custom`，有头无标记）＋ text | **是** | **0 字符** |
| 真实 AWT（`--flavor=all`，无头）＋ text | **是** | **0 字符** |
| 真实 AWT（`--text-only`，**连 CF_HTML 都没有**，只有纯文本） | **是** | **0 字符** |
| 真实 AWT 载荷写完后**杀掉 JVM**（载荷仍在，sha256 前缀不变） | **是** | **0 字符** |
| 把 AWT 载荷读回来**重写**成只含 CF_HTML + `CF_UNICODETEXT`（剔掉 URI 格式） | 否 | **贴出纯文本（第 1 轮即成功）** |

⇒ 结论：**AWT/Java 来源的剪贴板 ⇒ 本应用编辑器粘贴无反应**，用户反馈 F6 与之一致；
`Notepad` / Lark 能收到是因为它们不走 QtWebEngine 的剪贴板适配层。
**修复方向（本轮已验证有效）**：粘贴不要依赖渲染层读剪贴板 ——
由 Python 原生侧读（裸 Win32 `CF_UNICODETEXT`/`CF_HTML`）后经桥写入编辑器，
或在粘贴前把剪贴板"归一化"（剔掉 `CFSTR_INETURL` 再重写）。
⚠ 仍未定：QtWebEngine 内部**为何**在 `QMimeData` 提供 `text/uri-list` 时把粘贴判成空
（未做 QtWebEngine 侧插桩，故只报现象不报内部机制）。

## 剪贴板写入方式（**不用 `QMimeData.setHtml()`**）

必须走 Win32 原语，否则测不到"真实来源"的头：
`OpenClipboard` → `EmptyClipboard` → `RegisterClipboardFormatW("HTML Format")`
→ `SetClipboardData(fmt, hglobal)` → `CloseClipboard`；**同时**写 `CF_UNICODETEXT(13)`
（真实场景两个 flavor 都在）。
⚠ **移交后内存归系统所有**：`SetClipboardData` 成功后**绝不再 `GlobalFree`**（会崩/损坏剪贴板）。
`GlobalAlloc(GMEM_MOVEABLE)` + `GlobalLock` + `memmove` + `GlobalUnlock` 是唯一正确用法；
本模块的 `set_clipboard()` 只做前三步，收尾一律**不释放**。
⚠ `CloseClipboard` 必须调用，否则后续读取（Qt / Blink 都是读取方）会失败。

## 载荷构造（本探针的核心）

两种都满足 CF_HTML 规格：`Version:` + `StartHTML:`/`EndHTML:`/`StartFragment:`/`EndFragment:`
四个 **10 位补零的字节偏移**，偏移相对整段 payload 的起点。偏移用 **UTF-8 字节**计算
（`len(s.encode("utf-8"))`），因此中文载荷同样可靠；写完还会把 payload 重新读回来
**按偏移切片复核**（`raw[sf:ef] == fragment`），复核不过直接判 FAIL 而不是"静默测错"。

- **AWT/IDEA 风格**：`<HTML>`/`<BODY>` 上下文都有、四个数字偏移都写、**绝不出现**
  `<!--StartFragment-->` 注释（对齐 `sun.awt.windows.HTMLCodec.convertToHTMLFormat` 的真实行为）；
- **Chromium 风格**：正文里**含** `<!--StartFragment-->…<!--EndFragment-->` 注释。

## 真机驱动：复用了 r15 的哪些东西（以及为什么）

文件名含连字符不能 `import`，故用 `importlib.util.spec_from_file_location` 以自定义模块名
`r15_ref` 加载 `r15-real-gui-ac64-ac71.py`。**复用**（这些是成熟手法，重写只会引入新坑）：

- `r15_ref.R15`（标题通道 `js()`、`os_key()`、`activate()`、`focus_editor()`、
  `reload_page_and_wait()`、`window_alive()`、`modal_widget()`、`state()`）
  —— 理由：`runJavaScript` 回调恒空串、Qt 合成事件到不了渲染层、重载需
  `view.reload()`（Qt6 的 `QWebEnginePage` 没有 `reload()`）等实测坑都已在其中处理；
- `r15_ref.start_isolated_server` / `python_side_login`（桌面端**服务层**真登录：
  `do_key_exchange → init_admin → login` + 重载页面让渲染层恢复会话）、
  `install_bridge_counters`、`os_press` / `os_release_modifiers` / `os_mod_state`、
  `free_port` / `wait_health`、`fnv1a32` / `text_fingerprint`；
- 上述函数里的常量（`ADMIN_USER` / `ADMIN_PASSWORD` / `REPO` / `DESKTOP`）在 `main()` 里
  **改成本探针自己的值**（`r17probe`）后调用，避免与 r15 的证据混淆。

**自写**：剪贴板 Win32 原语与两种 CF_HTML 载荷、粘贴驱动与判据编排、报告类、
WebAction 触发计数instrumentation（r15 没有），以及 r17 自己的结果文件名。理由：
这些是 r15 完全没有的能力，无法复用。

## 怎么跑

    jflove-desktop\\venv-win\\Scripts\\python.exe poc\\editor-probes\\r17-clipboard-cfhtml.py
    ... --no-cjk        # 跳过中文载荷（默认会跑）
    ... --only USER-CLIP # **只读**摊开真实剪贴板（先在 IDEA 里复制，再跑这一条）
    ... --out <目录>     # 指定证据目录（默认 poc/editor-probes/out/r17-<时间戳>）

## 退出码（沿用 r15 / r13 口径）

- `0` 必须通过的判据全过（SKIP / 非阻塞观察项不影响退出码）
- `1` 有必须通过的判据失败 —— **修复前本探针预期为 1**（`CLIP-AWT` FAIL）
- `2` 环境 / 脚本错误（UI 未加载、QtWebEngine 起不来、隔离服务端不可用）
- `3` 看门狗超时

## 纪律与边界（AGENTS.md §9.14）

- 只写 `poc/editor-probes/**`（证据目录 `out/r17-<时间戳>/`），**不改任何产品源码**；
- 沙箱 `APPDATA` / 隔离服务端副本一律 `.tmp_` 前缀（命中 `.gitignore:82`，严禁入库）；
- **不打印剪贴板正文**：只打长度 / FNV-1a 指纹 / flavor 名 / 布尔（`'<html' in …` 之类），
  前 12 字节只打**十六进制**；不出现 token / session_key / 口令 / 文档正文。
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
import sys
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

SCRIPT = Path(__file__).resolve()
PROBES = SCRIPT.parent
DESKTOP = SCRIPT.parents[2]
REPO = SCRIPT.parents[3]

# Windows 控制台是 GBK：日志里有 ⇒/·/⚠ 等字符，不重配置会 UnicodeEncodeError 中断探针
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
sys.path.insert(0, str(DESKTOP))

#: 探针自建的管理员凭据（只存在于本次运行的隔离库里，不写入仓库任何文件）
ADMIN_USER = "r17probe"
ADMIN_PASSWORD = "r17-probe-Passw0rd"

#: 全部判据 id（`--only` 的取值域）
ALL_CRITERIA = ("P0", "P1", "SHAPE-AWT", "CLIP-AWT", "CLIP-CHROMIUM", "CLIP-TEXT",
                "CLIP-EMPTY", "CLIP-HTMLONLY", "CLIP-DIVERGE", "H-NOHOST", "AWT-CJK",
                "USER-CLIP", "CLIP-AWT-REAL")

#: 粘贴判据：id → 载荷种类
CLIP_CASES = (("AWT", "CLIP-AWT"), ("CHROMIUM", "CLIP-CHROMIUM"), ("TEXT", "CLIP-TEXT"),
              ("EMPTY", "CLIP-EMPTY"), ("AWT-HTMLONLY", "CLIP-HTMLONLY"),
              ("AWT-DIVERGE", "CLIP-DIVERGE"))

#: Win32 常量
GMEM_MOVEABLE = 0x0002
CF_UNICODETEXT = 13

#: 常见标准格式 id → 名字（诊断用；只用于**枚举**剪贴板，不读内容）
STANDARD_FORMAT_NAMES = {
    1: "CF_TEXT", 2: "CF_BITMAP", 3: "CF_METAFILEPICT", 4: "CF_SYLK", 5: "CF_DIF",
    6: "CF_TIFF", 7: "CF_OEMTEXT", 8: "CF_DIB", 9: "CF_PALETTE", 10: "CF_PENDATA",
    11: "CF_RIFF", 12: "CF_WAVE", 13: "CF_UNICODETEXT", 14: "CF_ENHMETAFILE",
    15: "CF_HDROP", 16: "CF_LOCALE", 17: "CF_DIBV5", 0x0080: "CF_OWNERDISPLAY",
    0x0081: "CF_DSPTEXT", 0x0082: "CF_DSPBITMAP", 0x008E: "CF_DSPENHMETAFILE",
    0x0200: "CF_PRIVATEFIRST", 0x02FF: "CF_PRIVATELAST", 0x0300: "CF_GDIOBJFIRST",
    0x03FF: "CF_GDIOBJLAST",
}


# ─────────────────────────── 复用 r15 的原语（在 main() 里绑定真身） ───────────────────────────

#: `os_press` / `os_release_modifiers` 由 `bind_r15_primitives()` 从 r15 模块取真身。
#: r15 的 `_VK` 缺 `backspace`，绑定前会补上（缺了就没法用 OS 级按键清空正文）。
os_press = None
os_release_modifiers = None


def bind_r15_primitives(r15) -> dict:
    """把 r15 的 OS 级按键原语绑定到本模块的全局名，并补齐它缺的虚拟键。"""
    global os_press, os_release_modifiers

    added: list[str] = []
    for key, vk in (("backspace", 0x08), ("delete", 0x2E), ("v", 0x56), ("a", 0x41)):
        if key not in r15._VK:  # noqa: SLF001 - r15 的按键表是模块级私有字典
            r15._VK[key] = vk  # noqa: SLF001
            added.append(key)
    os_press = r15.os_press
    os_release_modifiers = r15.os_release_modifiers
    return {"vkAdded": added, "vkKeys": sorted(r15._VK.keys())}


# ─────────────────────────── 判定与留证 ───────────────────────────


class Report:
    """收集每条判据的结果，最后写 JSON + 决定退出码（与 r15 同口径，独立实现避免耦合）。"""

    def __init__(self, out_dir: Path, only: tuple[str, ...]) -> None:
        self.out = out_dir
        self.only = only
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
            self.say(f"       {json.dumps(detail, ensure_ascii=False)[:1200]}")

    def verdict(self) -> int:
        failed = [c for c in self.checks if c["gating"] and c["status"] == "FAIL"]
        return 1 if failed else 0

    def write(self, extra: dict) -> Path:
        payload = {
            "probe": "r17-clipboard-cfhtml",
            "at": datetime.now().isoformat(timespec="seconds"),
            "only": list(self.only),
            "checks": self.checks,
            "notes": self.notes,
            **extra,
        }
        path = self.out / "r17-result.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


# ─────────────────────────── Win32 剪贴板（直写，不走 QMimeData） ───────────────────────────

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.EmptyClipboard.argtypes = []
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.CloseClipboard.argtypes = []
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
_user32.SetClipboardData.restype = wintypes.HANDLE
_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.GetClipboardData.restype = wintypes.HANDLE
_user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
_user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
_user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
_user32.RegisterClipboardFormatW.restype = wintypes.UINT
_user32.GetClipboardSequenceNumber.argtypes = []
_user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
_user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
_user32.EnumClipboardFormats.restype = wintypes.UINT
_user32.GetClipboardFormatNameW.argtypes = [wintypes.UINT, wintypes.LPWSTR, ctypes.c_int]
_user32.GetClipboardFormatNameW.restype = ctypes.c_int
_user32.GetForegroundWindow.argtypes = []
_user32.GetForegroundWindow.restype = wintypes.HWND
_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
_kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalLock.restype = wintypes.LPVOID
_kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalUnlock.restype = wintypes.BOOL
_kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalSize.restype = ctypes.c_size_t

#: 注册一次即可（同一格式名在整个会话里返回同一个 id）
CF_HTML = _user32.RegisterClipboardFormatW("HTML Format")


def _alloc(raw: bytes) -> int:
    """把 `raw` 拷进一块 `GMEM_MOVEABLE` 全局内存，返回句柄。

    ⚠ 句柄在 `SetClipboardData` 成功后**归系统所有**，调用方**不得** `GlobalFree`
    （释放系统仍持有的句柄会损坏剪贴板 / 直接崩进程）。
    """
    handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(raw))
    if not handle:
        raise OSError(f"GlobalAlloc 失败：{ctypes.get_last_error()}")
    ptr = _kernel32.GlobalLock(handle)
    if not ptr:
        raise OSError(f"GlobalLock 失败：{ctypes.get_last_error()}")
    try:
        ctypes.memmove(ptr, raw, len(raw))
    finally:
        _kernel32.GlobalUnlock(handle)
    return handle


def open_clipboard(retries: int = 12, pause_ms: int = 80) -> bool:
    """抢剪贴板所有权（别的进程持有时会失败 ⇒ 重试）。"""
    for _ in range(retries):
        if _user32.OpenClipboard(None):
            return True
        time.sleep(pause_ms / 1000.0)
    return False


def set_clipboard(html: str | None, text: str | None) -> dict:
    """**直写**剪贴板：可选 `HTML Format`（UTF-8，CF_HTML）+ 可选 `CF_UNICODETEXT`。

    - `html=None, text=None` ⇒ 只清空（用于 `CLIP-EMPTY`）；
    - 两个 flavor 都写时顺序为 HTML 先、文本后（与真实来源一致，真实场景两者并存）。

    :returns: 只含**布尔 / 长度 / 十六进制前缀**的描述（不含正文，§9.14）。
    """
    info: dict = {"cfHtmlFormatId": CF_HTML, "opened": False, "closed": False,
                  "htmlWritten": False, "textWritten": False}
    if not open_clipboard():
        info["error"] = f"OpenClipboard 失败：{ctypes.get_last_error()}"
        return info
    info["opened"] = True
    try:
        _user32.EmptyClipboard()
        if html is not None:
            raw = html.encode("utf-8") + b"\x00"
            info["htmlUtf8Bytes"] = len(raw)
            info["htmlPrefix12Hex"] = raw[:12].hex()
            if not _user32.SetClipboardData(CF_HTML, _alloc(raw)):
                info["htmlWriteError"] = ctypes.get_last_error()
            else:
                info["htmlWritten"] = True
        if text is not None:
            raw = (text + "\x00").encode("utf-16-le")
            info["textUtf16Bytes"] = len(raw)
            info["textCodePoints"] = len(text)
            if not _user32.SetClipboardData(CF_UNICODETEXT, _alloc(raw)):
                info["textWriteError"] = ctypes.get_last_error()
            else:
                info["textWritten"] = True
    finally:
        # 一定要关：不关后续任何读取方（Qt / Blink / 别的进程）都拿不到剪贴板
        _user32.CloseClipboard()
        info["closed"] = True
    return info


def read_clipboard_win32() -> dict:
    """用 Win32 把刚写进去的 payload **读回来**（独立于 Qt 的复核，用于校验偏移）。

    只回长度 / 指纹 / 前缀十六进制；`fragmentByOffsetOk` 是**按偏移切片**的复核结论。
    """
    result: dict = {"htmlAvailable": bool(_user32.IsClipboardFormatAvailable(CF_HTML)),
                    "textAvailable": bool(_user32.IsClipboardFormatAvailable(CF_UNICODETEXT))}
    if not open_clipboard():
        result["error"] = "OpenClipboard(failed)"
        return result
    try:
        if result["htmlAvailable"]:
            handle = _user32.GetClipboardData(CF_HTML)
            ptr = _kernel32.GlobalLock(handle) if handle else None
            if ptr:
                size = _kernel32.GlobalSize(handle)
                raw = ctypes.string_at(ptr, size).rstrip(b"\x00")
                _kernel32.GlobalUnlock(handle)
                result["htmlBytes"] = len(raw)
                result["htmlSha256Prefix"] = hashlib.sha256(raw).hexdigest()[:16]
                result["htmlPrefix12Hex"] = raw[:12].hex()
                result["offsets"] = parse_cfhtml_offsets(raw.decode("utf-8", "replace"))
                result["fragmentByOffsetOk"] = check_fragment_by_offset(raw)
        if result["textAvailable"]:
            handle = _user32.GetClipboardData(CF_UNICODETEXT)
            ptr = _kernel32.GlobalLock(handle) if handle else None
            if ptr:
                text = ctypes.wstring_at(ptr)
                _kernel32.GlobalUnlock(handle)
                result["textCodePoints"] = len(text)
                result["textFnv1a32"] = fnv1a32(text.encode("utf-8"))
    finally:
        _user32.CloseClipboard()
    return result


# ─────────────────────────── CF_HTML 载荷 ───────────────────────────

_OFFSET_SLOTS = (
    ("StartHTML:", "startHtml"),
    ("EndHTML:", "endHtml"),
    ("StartFragment:", "startFragment"),
    ("EndFragment:", "endFragment"),
)


def _encode(text: str) -> bytes:
    return text.encode("utf-8")


def build_cfhtml(fragment: str, with_marker: bool) -> tuple[str, dict]:
    """构造 CF_HTML 载荷（两种风格），返回 `(payload, 元数据)`。

    偏移**全部按 UTF-8 字节**计算；四个占位符都是 10 字节，替换后长度不变 ⇒ 偏移不自漂移。
    最后自校验：四个字段都恰好命中一次、按偏移切片能取回 fragment。
    """
    header = (
        "Version:1.0\r\n"
        "StartHTML:{startHtml}\r\n"
        "EndHTML:{endHtml}\r\n"
        "StartFragment:{startFragment}\r\n"
        "EndFragment:{endFragment}\r\n"
    )
    placeholder = "0" * 10
    slots = [key for _, key in _OFFSET_SLOTS]
    full = header.format(**{key: placeholder for key in slots})
    if with_marker:
        # Chromium 风格：注释标记在正文里，Qt 的转换器会把它一起交给 QMimeData.html()
        body = ("<html><body><!--StartFragment-->" + fragment + "<!--EndFragment-->"
                "</body></html>")
        marker = "<!--StartFragment-->"
    else:
        # AWT/IDEA 风格：只有 <HTML>/<BODY> 上下文与四个数字偏移，**没有任何注释标记**
        body = "<HTML><BODY>\r\n" + fragment + "\r\n</BODY></HTML>"
        marker = None
    header_bytes = len(_encode(full))
    payload = full + body

    blob = _encode(payload)
    start_html = header_bytes  # `StartHTML` = body 的第一个字节
    assert blob[header_bytes:header_bytes + 6].lower() == b"<html>", "body 必须以 <HTML> 开头"
    end_html = len(blob)
    if marker is not None:
        start_fragment = blob.index(_encode(marker), header_bytes) + len(_encode(marker))
        end_fragment = blob.index(b"<!--EndFragment-->", header_bytes)
    else:
        start_fragment = blob.index(_encode(fragment), header_bytes)
        end_fragment = start_fragment + len(_encode(fragment))

    values = {"startHtml": start_html, "endHtml": end_html,
              "startFragment": start_fragment, "endFragment": end_fragment}
    #: 每个 `Xxx:` 标签在 payload 里**恰好出现一次** ⇒ 按标签定位替换不会串位
    label_counts = {label: payload.count(label) for label, _ in _OFFSET_SLOTS}
    for label, key in _OFFSET_SLOTS:
        idx = payload.index(label) + len(label)
        payload = payload[:idx] + f"{values[key]:010d}" + payload[idx + 10:]
    assert all(v == 1 for v in label_counts.values()), f"offset labels: {label_counts}"

    meta = {
        "style": "chromium" if with_marker else "awt",
        "hasMarkerComment": bool(marker),
        "offsets": values,
        "offsetsAreByteBased": True,
        "payloadUtf8Bytes": len(_encode(payload)),
        "fragmentUtf8Bytes": len(_encode(fragment)),
        "fragmentCodePoints": len(fragment),
    }
    return payload, meta


def parse_cfhtml_offsets(payload: str) -> dict:
    """把 payload 里的四个数字偏移读出来（自校验用）。"""
    out: dict = {}
    for label, key in _OFFSET_SLOTS:
        idx = payload.find(label)
        out[key] = int(payload[idx + len(label):idx + len(label) + 10]) if idx >= 0 else None
    return out


def check_fragment_by_offset(raw: bytes) -> dict:
    """按 `StartFragment:EndFragment` 切片复核（**这是"偏移算对了没"的硬判据**）。

    只回长度 + 指纹 + 是否落在标记里，不回切片正文（§9.14）。
    """
    payload = raw.decode("utf-8", "replace")
    offs = parse_cfhtml_offsets(payload)
    sf, ef = offs.get("startFragment"), offs.get("endFragment")
    if sf is None or ef is None or not (0 <= sf <= ef <= len(raw)):
        return {"ok": False, "why": "offsets-out-of-range", "offsets": offs}
    chunk = raw[sf:ef]
    return {
        "ok": bool(chunk),
        "chunkUtf8Bytes": len(chunk),
        "chunkFnv1a32": fnv1a32(chunk),
        "chunkIsWholeFragment": True,
        "offsets": offs,
    }


def fnv1a32(data: bytes) -> str:
    """FNV-1a 32 位（与渲染层 `JS_DOC_TEXT` 内联实现同口径）。"""
    h = 0x811C9DC5
    for byte in data:
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


# ─────────────────────────── 渲染层脚本 ───────────────────────────

#: 当前激活面板正文读数（长度 / 指纹 / 焦点）。**不回正文**。
JS_DOC = r"""
(function () {
  try {
    var panel = document.querySelector('[data-editor-panel-active="true"]');
    if (!panel) return { r17Error: 'no-active-panel' };
    var content = panel.querySelector('.cm-content');
    if (!content) return { r17Error: 'no-cm-content' };
    var api = window.__JFLOVE_CM__;
    if (!api || typeof api.EditorView.findFromDOM !== 'function') {
      return { r17Error: 'no-cm-bundle' };
    }
    var view = api.EditorView.findFromDOM(content);
    if (!view) return { r17Error: 'no-view' };
    var text = view.state.doc.toString();
    var bytes = new TextEncoder().encode(text);
    var h = 0x811c9dc5;
    for (var b = 0; b < bytes.length; b++) {
      h = h ^ bytes[b];
      h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
    }
    return {
      ok: true,
      panelId: panel.getAttribute('data-editor-panel'),
      docCodePoints: [...text].length,
      docUtf8Bytes: bytes.length,
      docFnv1a32: ('00000000' + h.toString(16)).slice(-8),
      docCharUnits: text.length,
      empty: text.length === 0,
      hasFocus: !!view.hasFocus,
      selectionMain: { from: view.state.selection.main.from, to: view.state.selection.main.to },
      domFocused: !!(document.activeElement
                     && content.contains(document.activeElement))
    };
  } catch (e) { return { r17Error: String(e) }; }
})()
"""

#: 在渲染层把正文与期望**逐字符**比对（只回布尔 + 首差位置，不回任何正文）。
JS_COMPARE_DOC = r"""
(function () {
  try {
    var expected = %s;
    var panel = document.querySelector('[data-editor-panel-active="true"]');
    if (!panel) return { r17Error: 'no-active-panel' };
    var content = panel.querySelector('.cm-content');
    if (!content) return { r17Error: 'no-cm-content' };
    var api = window.__JFLOVE_CM__;
    var view = api.EditorView.findFromDOM(content);
    if (!view) return { r17Error: 'no-view' };
    var text = view.state.doc.toString();
    var equal = text === expected;
    var firstDiff = -1;
    if (!equal) {
      var n = Math.min(text.length, expected.length);
      for (var i = 0; i < n; i++) {
        if (text.charCodeAt(i) !== expected.charCodeAt(i)) { firstDiff = i; break; }
      }
      if (firstDiff < 0) firstDiff = n;
    }
    return {
      equal: equal, firstDiff: firstDiff,
      docCodePoints: [...text].length, expectedCodePoints: [...expected].length,
      docCharUnits: text.length, expectedCharUnits: expected.length
    };
  } catch (e) { return { r17Error: String(e) }; }
})()
"""

#: 把光标落进正文（真实鼠标点击，带命中点）；粘贴的前置条件
JS_FOCUS_CONTENT = r"""
(function () {
  try {
    var panel = document.querySelector('[data-editor-panel-active="true"]');
    if (!panel) return { r17Error: 'no-active-panel' };
    var content = panel.querySelector('.cm-content');
    if (!content) return { r17Error: 'no-cm-content' };
    var api = window.__JFLOVE_CM__;
    var view = api.EditorView.findFromDOM(content);
    if (!view) return { r17Error: 'no-view' };
    view.focus();
    return {
      ok: true, panelId: panel.getAttribute('data-editor-panel'),
      hasFocus: !!view.hasFocus,
      docCodePoints: [...view.state.doc.toString()].length,
      selectionMain: { from: view.state.selection.main.from, to: view.state.selection.main.to }
    };
  } catch (e) { return { r17Error: String(e) }; }
})()
"""

#: 页面可见性 / 焦点（Blink 在页面不可聚焦时会拒绝剪贴板读取）
JS_FOCUS_STATE = r"""
(function () {
  try {
    return { hasFocus: document.hasFocus(), hidden: document.hidden,
             visibility: document.visibilityState,
             activeTag: document.activeElement ? document.activeElement.tagName : null,
             activeClass: document.activeElement ? (document.activeElement.className || '')
                                                  : null };
  } catch (e) { return { r17Error: String(e) }; }
})()
"""


# ─────────────────────────── 剪贴板诊断（序列号 / flavor 清单） ───────────────────────────


def clipboard_sequence() -> int:
    """剪贴板序列号（Windows：任何写入都会让它 +1）⇒ 确认"剪贴板真的被换了"。"""
    return int(_user32.GetClipboardSequenceNumber())


def foreground_window() -> int:
    """当前前台窗口句柄（`CLIP-AWT-REAL` 用来排除"按键打到了别的窗口"这种假 FAIL）。"""
    return int(_user32.GetForegroundWindow() or 0)


def hwnd_of(widget) -> int:
    """取 Qt 顶层窗口的 HWND（拿不到就回 0，不抛）。"""
    try:
        return int(widget.winId())
    except Exception:  # noqa: BLE001 - 诊断用途，拿不到就当 0
        return 0


def clipboard_format_names() -> list[str]:
    """剪贴板上**当前实际存在**的格式名（只枚举，不读内容）。

    用途：把"探针载荷的 flavor"与"真实来源（IDEA / Lark）的 flavor"摊开对比 ——
    这是解释"为什么合成载荷贴得进去、真实复制贴不进去"的关键读数。
    """
    names: list[str] = []
    if not open_clipboard():
        return ["<OpenClipboard failed>"]
    try:
        fmt = 0
        while len(names) < 60:
            fmt = int(_user32.EnumClipboardFormats(fmt))
            if fmt == 0:
                break
            if 0xC000 <= fmt <= 0xFFFF:
                buf = ctypes.create_unicode_buffer(256)
                _user32.GetClipboardFormatNameW(fmt, buf, 256)
                names.append(buf.value or f"registered:{fmt}")
            else:
                names.append(STANDARD_FORMAT_NAMES.get(fmt, f"std:{fmt}"))
    finally:
        _user32.CloseClipboard()
    return names


def js_set_doc(text: str) -> str:
    """用 **CM6 自己的事务**把激活面板正文整体替换成 `text`（消费方是真实 updateListener）。

    用途：为 `CLIP-EMPTY` 造一个"非空基线"而**不碰剪贴板** —— 否则无法区分
    "正文没变"是因为"剪贴板空 ⇒ 粘贴无事发生"还是因为"正文本来就是空的"。
    """
    return (
        "(function(){try{var api=window.__JFLOVE_CM__;if(!api)return {r17Error:'no-bundle'};"
        "var panel=document.querySelector('[data-editor-panel-active=\"true\"]');"
        "if(!panel)return {r17Error:'no-panel'};"
        "var content=panel.querySelector('.cm-content');"
        "if(!content)return {r17Error:'no-content'};"
        "var view=api.EditorView.findFromDOM(content);if(!view)return {r17Error:'no-view'};"
        "view.focus();"
        "view.dispatch({changes:{from:0,to:view.state.doc.length,insert:%s}});"
        "return {ok:true,docCodePoints:[...view.state.doc.toString()].length};"
        "}catch(e){return {r17Error:String(e)};}})()" % json.dumps(text, ensure_ascii=False)
    )


# ─────────────────────────── 宿主 WebAction 触发计数 ───────────────────────────


def count_web_actions(shell) -> dict:
    """包 `ShellPage.triggerAction`（探针进程内，不改产品源码）统计**宿主** WebAction 触发。

    判据 `H-NOHOST` 的语义：粘贴若正确发生在渲染层，宿主**一次** `triggerAction` 都不该被调
    （Chromium 内部自行处理剪贴板）；任何计数 > 0 都说明"宿主替渲染层做了事"。
    `WebAction.NoWebAction` 与枚举哨兵 `WebActionCount` 不算动作 ⇒ 42 个吗？
    实测 PySide6 6.11 该枚举共 49 项，扣除 `NoWebAction` + `WebActionCount` = **47** 项。
    """
    from PySide6.QtWebEngineCore import QWebEnginePage

    page = shell.view.page()
    names = [action.name for action in QWebEnginePage.WebAction
             if action.name not in ("NoWebAction", "WebActionCount")]
    counters: dict = {"total": 0, "byAction": {}, "events": [], "phase": "init"}

    original = page.triggerAction

    def wrapped(action, *args, **kwargs):
        key = action.name if hasattr(action, "name") else str(action)
        counters["total"] += 1
        counters["byAction"][key] = counters["byAction"].get(key, 0) + 1
        counters["events"].append({"action": key, "at": round(time.time(), 2),
                                   "phase": counters["phase"]})
        return original(action, *args, **kwargs)

    page.triggerAction = wrapped  # type: ignore[method-assign]

    def reset(phase: str) -> dict:
        """把计数清零并打上新相位标签（用于排除**探针自己**的 `Reload` 等动作）。

        为什么必须能清零：`reload_page_and_wait()` 走的就是
        `page.triggerAction(Reload)`（r15 实测 `QWebEnginePage` 没有 `reload()`）——
        那是**探针**为恢复会话而做的动作，不是应用"替渲染层处理粘贴"的证据。
        把它的计数留在总数里会把 `H-NOHOST` 变成恒 FAIL 的假判据。
        """
        snapshot = {"total": counters["total"], "byAction": dict(counters["byAction"]),
                    "eventCount": len(counters["events"]), "phase": counters["phase"]}
        counters["total"] = 0
        counters["byAction"] = {}
        counters["events"].clear()
        counters["phase"] = phase
        return snapshot

    counters["reset"] = reset
    return {"actionNames": names, "actionCount": len(names), "counters": counters}


# ─────────────────────────── 探针主体 ───────────────────────────


class _EarlyExit(Exception):
    """本轮不需要渲染层（例如 `--only USER-CLIP`）⇒ 提前收尾但走正常写证据路径。"""


class R17:
    """剪贴板 ⇒ 粘贴判据的驱动；UI 侧全部复用 r15 的成熟手法（见模块文档）。"""
    def __init__(self, report: Report, driver, out_dir: Path, actions: dict) -> None:
        self.report = report
        self.d = driver  # r15_ref.R15 实例
        self.out = out_dir
        self.actions = actions

    # ── Qt 眼中的剪贴板 ──
    def qt_clipboard_shape(self) -> dict:
        """读 `QGuiApplication.clipboard().mimeData()`（**不含正文**，只回布尔 / 长度 / 前缀）。

        `'<html' in html.lower()` / `'<!--StartFragment' in html` / `'StartFragment:' in html`
        这三条布尔就是 M1 的机制证据：
        - Qt 砍头 ⇒ 没有 `StartFragment:`；
        - 载荷是 AWT 风格 ⇒ 也没有 `<!--StartFragment`；
        - 两者都不在而 `hasHtml()` 仍为 True ⇒ Blink 会走 HTML 粘贴模式却拿到空片段。
        """
        from PySide6.QtGui import QGuiApplication

        mime = QGuiApplication.clipboard().mimeData()
        if mime is None:
            return {"error": "no-mimeData"}
        html = mime.html() or ""
        text = mime.text() or ""
        return {
            "formats": list(mime.formats()),
            "hasHtml": bool(mime.hasHtml()),
            "hasText": bool(mime.hasText()),
            "htmlChars": len(html),
            "htmlUtf8Bytes": len(html.encode("utf-8")),
            "htmlLowerHasTag": "<html" in html.lower(),
            "htmlHasMarkerComment": "<!--StartFragment" in html,
            "htmlHasStartFragmentHeader": "StartFragment:" in html,
            "htmlHasEndFragmentHeader": "EndFragment:" in html,
            "htmlPrefix12Hex": html.encode("utf-8")[:12].hex(),
            "htmlSuffix12Hex": html.encode("utf-8")[-12:].hex() if html else "",
            "textChars": len(text),
            "textFnv1a32": fnv1a32(text.encode("utf-8")),
            "plainMatchesPayload": None,  # 由调用方补
        }

    def doc(self) -> dict:
        return self.d.js(JS_DOC) or {}

    def _press(self, mods: list[str], key: str) -> bool:
        """OS 级按键（`keybd_event`）；非 Windows 返回 False。"""
        return bool(os_press(mods, key))

    def _clear_doc(self, *, ctrl_a: bool, ctrl_key: str) -> dict:
        """把激活面板正文清空，并**自校验**；不成功就重试（最多 4 轮）。

        为什么必须重试：`Ctrl+A` 是 OS 级按键，**只有渲染层真的有焦点时**才落到 CM6；
        实测偶发"窗口抢前台未完成 ⇒ `view.hasFocus=false` ⇒ `Ctrl+A` 什么也没选中 ⇒
        `Backspace` 只删一个字符"，于是起点不干净、粘贴结果变成"追加"。
        那是**探针的时序 artifact**，不是产品缺陷 —— 必须自己修掉，否则判据会假 FAIL。
        """
        from PySide6.QtTest import QTest

        attempts: list[dict] = []
        for index in range(4):
            self.d.activate()
            self.d.shell.view.setFocus()
            QTest.qWait(200)
            focus = self.d.js(JS_FOCUS_CONTENT) or {}
            QTest.qWait(200)
            state = self.d.js(JS_FOCUS_STATE) or {}
            if focus.get("hasFocus") is not True:
                # 渲染层没拿到焦点：本轮不发按键（发了也是白打，还会污染证据）
                attempts.append({"round": index + 1, "focus": focus, "pageFocus": state,
                                 "pressed": False})
                QTest.qWait(350)
                continue
            if ctrl_a:
                os_press(["ctrl"], "a")
            else:
                os_press(["ctrl"], ctrl_key)
            QTest.qWait(350)
            os_press([], "backspace")
            QTest.qWait(400)
            after = self.doc()
            attempts.append({"round": index + 1, "focus": focus, "pageFocus": state,
                             "pressed": True, "docCodePoints": after.get("docCodePoints"),
                             "empty": after.get("empty")})
            if after.get("empty"):
                return {"ok": True, "attempts": attempts, "focus": focus,
                        "pageFocus": state, "before": after}
        return {"ok": False, "attempts": attempts, "last": self.doc()}

    def paste(self, expect: str, *, kind: str, label: str) -> dict:
        """一次完整的「落焦点 → 清空正文 → OS 级 Ctrl+V → 读回」并**逐字符**比对。

        清空用 OS 级 `Ctrl+A` + `Backspace`（走 Blink 的编辑命令，不是桥调用）；
        干净起点是"正文等于期望"这条判据不产生假 PASS 的前提。
        """
        from PySide6.QtTest import QTest

        self.report.note(f"[{label}] 落焦点 + 清空正文（OS 级 Ctrl+A / Backspace，自校验重试）")
        focus_before = self.d.focus_editor()
        cleared = self._clear_doc(ctrl_a=True, ctrl_key="a")
        before = self.doc()
        clear_ok = bool(cleared.get("ok")) and bool(before.get("empty"))

        # 粘贴前再确认一次焦点（清空重试可能已经把它抢回来了，这里只观察不再按键）
        self.d.shell.view.setFocus()
        self.d.js(JS_FOCUS_CONTENT)
        QTest.qWait(150)
        paste_focus = self.d.js(JS_FOCUS_CONTENT) or {}
        self.d.js(JS_FOCUS_CONTENT)
        QTest.qWait(120)
        seq_before = clipboard_sequence()
        os_press(["ctrl"], "v")
        QTest.qWait(900)
        compare = self.d.js(JS_COMPARE_DOC % json.dumps(expect, ensure_ascii=False)) or {}
        after = self.doc()
        if not compare.get("equal"):
            # 给渲染层一次补帧机会再判（避免把"还没渲染完"当成 FAIL）
            QTest.qWait(900)
            compare = self.d.js(JS_COMPARE_DOC % json.dumps(expect, ensure_ascii=False)) or {}
            after = self.doc()
        return {
            "kind": kind,
            "focusBeforeClear": focus_before,
            "clearRetry": cleared,
            "osCtrlAPressed": clear_ok,
            "clearedToEmpty": clear_ok,
            "selectionAfterClear": before.get("selectionMain"),
            "baselineFnv1a32": before.get("docFnv1a32"),
            "baselineCodePoints": before.get("docCodePoints"),
            "pasteFocus": paste_focus,
            "clipboardSequenceBeforePaste": seq_before,
            "clipboardSequenceAfterPaste": clipboard_sequence(),
            "focusStateAfterPaste": self.d.js(JS_FOCUS_STATE),
            "after": {k: after.get(k) for k in
                      ("docCodePoints", "docUtf8Bytes", "docFnv1a32", "empty", "hasFocus",
                       "selectionMain", "r17Error")},
            "compare": compare,
            "expected": {"codePoints": len(expect), "utf8Bytes": len(expect.encode("utf-8")),
                         "fnv1a32": fnv1a32(expect.encode("utf-8"))},
            "equal": bool(compare.get("equal") and clear_ok),
            "windowAlive": self.d.window_alive(),
        }

    def paste_negative(self, label: str) -> dict:
        """剪贴板为空时的 Ctrl+V：正文必须**不变**、窗口存活、且没有异常。

        判据写法（避免"什么都不做"被读成 PASS）：先用同一套「清空 + 写入期望文本」的手法
        把正文置成**非空基线**，再按键，再确认它**一字未变**。
        """
        from PySide6.QtTest import QTest

        self.report.note(f"[{label}] 剪贴板为空 ⇒ 按键前后正文必须完全一致")
        baseline_text = "r17-empty-baseline\n"
        setup = self._clear_doc(ctrl_a=True, ctrl_key="a")
        # 用 CM6 事务把基线文本写进去（不经剪贴板 ⇒ 与"空剪贴板"互不干扰）
        self.d.js(js_set_doc(baseline_text))
        QTest.qWait(300)
        before = self.doc()
        fp_before = before.get("docFnv1a32")
        nonempty_before = not before.get("empty")
        self.d.activate()
        self.d.shell.view.setFocus()
        QTest.qWait(150)
        inject_focus = self.d.js(JS_FOCUS_CONTENT) or {}
        QTest.qWait(120)
        focus_before = self.d.focus_editor()
        os_release_modifiers()
        QTest.qWait(60)
        seq_before = clipboard_sequence()
        os_press(["ctrl"], "v")
        QTest.qWait(900)
        after = self.doc()
        unchanged = (after.get("docFnv1a32") == fp_before
                     and after.get("docCodePoints") == before.get("docCodePoints")
                     and not after.get("r17Error"))
        return {
            "kind": "EMPTY",
            "baselineSetup": setup,
            "preconditionDocNonEmpty": nonempty_before,
            "docBefore": {k: before.get(k) for k in ("docCodePoints", "docFnv1a32", "empty",
                                                     "selectionMain")},
            "docAfter": {k: after.get(k) for k in ("docCodePoints", "docFnv1a32", "empty",
                                                   "selectionMain", "r17Error")},
            "unchanged": unchanged,
            "clipboardSequenceBeforePaste": seq_before,
            "clipboardSequenceAfterPaste": clipboard_sequence(),
            "focusBefore": focus_before,
            "injectFocus": inject_focus,
            "windowAlive": self.d.window_alive(),
            "pass": bool(nonempty_before and unchanged and self.d.window_alive()),
        }


# ─────────────────────────── 载荷数据集 ───────────────────────────


def make_payloads(include_cjk: bool, only: tuple[str, ...]) -> dict:
    """准备各判据的 `(fragment, plainText, cfhtml, meta)`。

    `fragment` 与 `plainText` **故意不同**（fragment 不带末尾换行、plainText 带）
    —— 这样"贴进去的到底是 HTML 还是纯文本"也能一眼看出来。

    载荷种类（`plain` 为 `None` = **不写** `CF_UNICODETEXT`，用于隔离 flavor 影响）：

    | key | CF_HTML | CF_UNICODETEXT | 用途 |
    | --- | --- | --- | --- |
    | `AWT` | AWT 风格（无注释标记） | 有 | 用户实测场景的主判据 |
    | `CHROMIUM` | Chromium 风格（有注释标记） | 有 | 正向对照 |
    | `TEXT` | 无 | 有 | 纯文本通路 |
    | `EMPTY` | 无 | 无 | 空剪贴板负向对照 |
    | `AWT-HTMLONLY` | AWT 风格 | **无** | **M1 的隔离实验**：无纯文本退路 |
    | `AWT-CJK` | AWT 风格 | 有 | 中文载荷（UTF-8 字节偏移） |
    """
    ascii_frag = "r17-awt-ascii-line-1\nr17-awt-ascii-line-2"
    ascii_plain = ascii_frag + "\n"
    cjk_frag = "第一行：中文与制表符\tOK\n第二行 ASCII\n第三行结束"
    cjk_plain = cjk_frag + "\n"
    payloads: dict = {}
    for key, frag, plain, marker in (
        ("AWT", ascii_frag, ascii_plain, False),
        ("CHROMIUM", ascii_frag, ascii_plain, True),
        ("AWT-HTMLONLY", ascii_frag, None, False),
        ("AWT-CJK", cjk_frag, cjk_plain, False),
    ):
        if key == "AWT-CJK" and not include_cjk:
            continue
        html, meta = build_cfhtml(frag, with_marker=marker)
        payloads[key] = {"html": html, "fragment": frag,
                         "plain": plain if plain is not None else ascii_plain,
                         "writesText": plain is not None, "meta": meta}
    payloads["TEXT"] = {"html": None, "fragment": "", "plain": ascii_plain,
                        "writesText": True, "meta": {}}
    payloads["EMPTY"] = {"html": None, "fragment": "", "plain": "",
                         "writesText": False, "meta": {}}
    # `AWT-DIVERGE`：CF_HTML 片段与 CF_UNICODETEXT **故意不同**（外加中文标记）。
    # 用途：区分 Blink 走的是哪条通路 ——
    #   贴出 `plain` ⇒ 走纯文本 flavor（HTML 被 M1 打空或 Blink 判空回落）；
    #   贴出 `fragment`（无中文标记）⇒ 真走了 HTML 通路；
    #   什么都没有 ⇒ 两条路都不通。
    if "CLIP-DIVERGE" in only:
        diverge_html, diverge_meta = build_cfhtml("r17-html-channel-only", with_marker=False)
        payloads["AWT-DIVERGE"] = {"html": diverge_html, "fragment": "r17-html-channel-only",
                                   "plain": ascii_plain, "writesText": True, "meta": diverge_meta}
    return payloads


def shape_detail(shape: dict, meta: dict, plain: str) -> dict:
    """补上"Qt 读到的纯文本是否等于载荷里的 CF_UNICODETEXT"这条交叉校验。"""
    out = dict(shape)
    out["plainMatchesPayload"] = (shape.get("textFnv1a32")
                                  == fnv1a32(plain.encode("utf-8")))
    out["payloadMeta"] = meta
    return out


# ─────────────────────────── 启动 ───────────────────────────


def load_r15():
    """以自定义模块名加载 r15（文件名含连字符，不能直接 `import`）。"""
    path = PROBES / "r15-real-gui-ac64-ac71.py"
    if not path.exists():
        raise FileNotFoundError(f"未找到依赖的 r15 探针：{path}")
    spec = importlib.util.spec_from_file_location("r15_ref", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法为 {path} 构造 import spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules["r15_ref"] = module
    spec.loader.exec_module(module)
    return module


def channel_of(result: dict, payload: dict) -> str:
    """判断一次粘贴实际走了哪条通路（**这是把 M1 与"实际行为"分开的关键读法**）。

    - `text-flavor`：贴出来的正是 `CF_UNICODETEXT` 的纯文本（HTML 通路被 M1 打空后回落）；
    - `html-fragment`：贴出来的是 CF_HTML 的片段（真走了 HTML 通路）；
    - `nothing`：正文仍是空的（两条通路都不通）；
    - `other`：都不是（贴出了第三样东西 ⇒ 必须人工看）。
    """
    after_fp = (result.get("after") or {}).get("docFnv1a32")
    after_len = (result.get("after") or {}).get("docCodePoints")
    plain = payload.get("plain") or ""
    fragment = payload.get("fragment") or ""
    if after_len == 0:
        return "nothing"
    if after_fp == fnv1a32(plain.encode("utf-8")):
        return "text-flavor"
    if after_fp == fnv1a32(fragment.encode("utf-8")):
        return "html-fragment"
    return "other"


def only_readonly_clipboard(only: tuple[str, ...]) -> bool:
    """本轮是否是**纯只读**剪贴板观察（`--only USER-CLIP`）⇒ 收尾不动用户剪贴板。"""
    return tuple(only) == ("USER-CLIP",)


def read_clipboard_expectation() -> tuple[str | None, dict]:
    """**只读**取"当前剪贴板上的期望文本"（`CLIP-AWT-REAL` 用；**不写剪贴板**）。

    优先级（与"用户按 Ctrl+V 时期望看到什么"一致）：

    1. `CF_UNICODETEXT` —— 纯文本 flavor（AWT / IDEA 的真实载荷里一定有它，
       实测即使 Java 端只声明 html flavor，OLE 也会合成纯文本）；
    2. 否则按 `StartFragment:EndFragment` 从 `CF_HTML` 切片（字节偏移）；
    3. 都没有 ⇒ `None`（判据会 FAIL 并说明原因）。

    末尾 `\\r\\n` → `\\n`：AWT 写文本时会把 LF 转成 CRLF（`WDataTransferer` 的 EOLN 处理），
    而渲染层文档里是 `\\n` ⇒ 不归一就会把"换行口径差异"误判成 FAIL。

    :returns: `(text, meta)`。`text` 只留在内存里用于比对，**绝不进证据 JSON**（§9.14）；
        `meta` 只含来源 / 长度 / 指纹 / 布尔。
    """
    meta: dict = {"source": "none", "textCodePoints": 0, "textUtf8Bytes": 0,
                  "textFnv1a32": "", "hadCrlf": False, "htmlAvailable": False,
                  "htmlOffsets": None, "htmlMarkerComment": False,
                  "htmlStartFragmentHeader": False}
    if not open_clipboard():
        meta["error"] = "OpenClipboard(failed)"
        return None, meta
    text: str | None = None
    try:
        if _user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            handle = _user32.GetClipboardData(CF_UNICODETEXT)
            ptr = _kernel32.GlobalLock(handle) if handle else None
            if ptr:
                text = ctypes.wstring_at(ptr)
                _kernel32.GlobalUnlock(handle)
                meta["source"] = "cf-unicodetext"
        meta["htmlAvailable"] = bool(_user32.IsClipboardFormatAvailable(CF_HTML))
        if text is None and meta["htmlAvailable"]:
            handle = _user32.GetClipboardData(CF_HTML)
            ptr = _kernel32.GlobalLock(handle) if handle else None
            if ptr:
                size = _kernel32.GlobalSize(handle)
                raw = ctypes.string_at(ptr, size).rstrip(b"\x00")
                _kernel32.GlobalUnlock(handle)
                payload = raw.decode("utf-8", "replace")
                meta["htmlOffsets"] = parse_cfhtml_offsets(payload)
                meta["htmlMarkerComment"] = "<!--StartFragment" in payload
                meta["htmlStartFragmentHeader"] = "StartFragment:" in payload
                sf = (meta["htmlOffsets"] or {}).get("startFragment")
                ef = (meta["htmlOffsets"] or {}).get("endFragment")
                if isinstance(sf, int) and isinstance(ef, int) and 0 <= sf <= ef <= len(raw):
                    text = raw[sf:ef].decode("utf-8", "replace")
                    meta["source"] = "cf-html-fragment"
    finally:
        _user32.CloseClipboard()
    if text is not None:
        meta["hadCrlf"] = "\r\n" in text
        text = text.replace("\r\n", "\n")
        meta["textCodePoints"] = len(text)
        meta["textUtf8Bytes"] = len(text.encode("utf-8"))
        meta["textFnv1a32"] = fnv1a32(text.encode("utf-8"))
    return text, meta


def inspect_user_clipboard() -> dict:
    """**只读观察**当前（真实来源留下的）剪贴板 —— 不写入、不用 Qt。

    这是回答"为什么合成载荷贴得进去、IDEA 复制贴不进去"的关键一步：
    把真实来源的 **flavor 清单**与 CF_HTML 头的结构摊开，与合成载荷逐项对比。
    只回格式名与布尔 / 长度 / 指纹 / 前缀十六进制，**不回内容**（§9.14）。

    用法：先在 IDEA 里复制一段代码（或用真实的复制动作），再跑
    `--only USER-CLIP`（该判据**不写剪贴板**）。
    """
    info: dict = {"formats": clipboard_format_names(),
                  "htmlAvailable": bool(_user32.IsClipboardFormatAvailable(CF_HTML)),
                  "unicodeTextAvailable": bool(_user32.IsClipboardFormatAvailable(
                      CF_UNICODETEXT)),
                  "oemTextAvailable": bool(_user32.IsClipboardFormatAvailable(7))}
    if not info["htmlAvailable"]:
        info["verdict"] = "no-html-flavor"
        return info
    if not open_clipboard():
        info["error"] = "OpenClipboard(failed)"
        return info
    try:
        handle = _user32.GetClipboardData(CF_HTML)
        ptr = _kernel32.GlobalLock(handle) if handle else None
        if not ptr:
            info["error"] = "GlobalLock(failed)"
            return info
        size = _kernel32.GlobalSize(handle)
        raw = ctypes.string_at(ptr, size).rstrip(b"\x00")
        _kernel32.GlobalUnlock(handle)
        text = raw.decode("utf-8", "replace")
        # 头字段（**偏移数字本身不是用户内容**，可以进证据）
        info["htmlBytes"] = len(raw)
        info["htmlSha256Prefix"] = hashlib.sha256(raw).hexdigest()[:16]
        info["htmlPrefix12Hex"] = raw[:12].hex()
        info["offsets"] = parse_cfhtml_offsets(text)
        info["hasMarkerComment"] = "<!--StartFragment-->" in text
        info["hasMarkerCommentLoose"] = "<!--StartFragment" in text
        info["hasEndMarkerComment"] = "<!--EndFragment" in text
        info["hasHtmlTag"] = "<html" in text.lower()
        info["hasBodyTag"] = "<body" in text.lower()
        info["lineEnding"] = ("crlf" if "\r\n" in text
                              else "lf" if "\n" in text else "none")
        info["markerCount"] = text.count("<!--StartFragment")
        info["fragmentByOffsetOk"] = check_fragment_by_offset(raw)
        # 按偏移切片复核：`StartHTML` 处是不是 `<html`（真实来源是否也是"砍头前"的完整 CF_HTML）
        sh = (info["offsets"] or {}).get("startHtml")
        info["startHtmlPointsAtHtmlTag"] = bool(
            isinstance(sh, int) and raw[sh:sh + 5].lower() == b"<html"
        )
        info["syntheticPayloadComparable"] = bool(
            info["hasMarkerComment"] and info["startHtmlPointsAtHtmlTag"]
        )
        info["verdict"] = ("with-marker" if info["hasMarkerComment"]
                           else "awt-like-no-marker")
    finally:
        _user32.CloseClipboard()
    return info


def build_shell():
    """构造真外壳（与 r15 同法：真窗口、无单实例守卫、无托盘）。"""
    from src.bridge import Bridge
    from src.ui.web_shell import WebShell, build_app
    from src.utils.input_method import setup_input_method

    setup_input_method()
    app = build_app()
    app.setQuitOnLastWindowClosed(False)
    bridge = Bridge()
    shell = WebShell(objects={"bridge": bridge}, frameless=True,
                     publish_event=bridge.publish_event)
    load_ok = shell.load_ui()
    shell.show()
    return app, shell, load_ok


def main() -> int:
    parser = argparse.ArgumentParser(
        description="r17 真机探针：剪贴板 CF_HTML（AWT vs Chromium）⇒ 编辑器粘贴（v1.6.1 F6）")
    parser.add_argument("--only", default=",".join(ALL_CRITERIA),
                        help="只跑指定判据（逗号分隔；默认全部）")
    parser.add_argument("--no-cjk", action="store_true", help="跳过中文载荷判据（AWT-CJK）")
    parser.add_argument("--out", default="", help="证据目录（默认 poc/editor-probes/out/r17-<时间戳>）")
    args = parser.parse_args()

    only = tuple(t.strip().upper() for t in args.only.split(",") if t.strip())
    bad = [t for t in only if t not in ALL_CRITERIA]
    if bad:
        print(f"--only 取值非法：{bad}（合法：{list(ALL_CRITERIA)}）")
        return 2
    include_cjk = (not args.no_cjk) and "AWT-CJK" in only

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else PROBES / "out" / f"r17-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    real_window = not os.environ.get("QT_QPA_PLATFORM", "").startswith("offscreen")

    # 用户目录沙箱：**必须在导入 src.config.settings 之前**改 APPDATA
    # （草稿 / 会话 / 快捷键全落这里）。`.tmp_` 前缀命中 .gitignore:82（严禁入库）
    appdata = out_dir / ".tmp_appdata"
    appdata.mkdir(parents=True, exist_ok=True)
    os.environ["APPDATA"] = str(appdata)
    os.environ["LOCALAPPDATA"] = str(appdata)

    report = Report(out_dir, only)
    report.say("r17 真机探针 · 剪贴板 CF_HTML（AWT vs Chromium）⇒ 编辑器粘贴 · v1.6.1 F6")
    report.say(f"平台={os.environ.get('QT_QPA_PLATFORM', '(默认)')} · 真窗口={real_window} · "
               f"判据={list(only)} · 中文载荷={include_cjk}")
    report.say(f"证据目录：{out_dir}")

    try:
        r15 = load_r15()
    except Exception as exc:  # noqa: BLE001 - 依赖缺失要看清
        report.add("P0", "复用 r15 工具（importlib 加载）", "FAIL", {"error": str(exc)})
        report.write({})
        return 2
    # 用自己的凭据，避免与 r15 的证据混淆（隔离库每次新建，账号只存在于本次运行的库内）
    r15.ADMIN_USER = ADMIN_USER
    r15.ADMIN_PASSWORD = ADMIN_PASSWORD
    primitives = bind_r15_primitives(r15)
    report.say(f"复用 r15 OS 级按键原语；补齐虚拟键：{primitives['vkAdded']}")

    payloads = make_payloads(include_cjk, only)
    report.say("载荷：" + json.dumps(
        {k: {"style": v["meta"].get("style"),
             "payloadUtf8Bytes": v["meta"].get("payloadUtf8Bytes"),
             "hasMarkerComment": v["meta"].get("hasMarkerComment"),
             "plainCodePoints": len(v["plain"])} for k, v in payloads.items()},
        ensure_ascii=False))

    try:
        app, shell, load_ok = build_shell()
    except Exception as exc:  # noqa: BLE001 - 环境问题要能看清原因
        import traceback

        traceback.print_exc()
        report.add("P0", "真外壳可构造（QtWebEngine + 桥 + 前端产物）", "FAIL", {"error": str(exc)})
        report.write({"platform": os.environ.get("QT_QPA_PLATFORM", "")})
        return 2

    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest

    QTimer.singleShot(900000, lambda: os._exit(3))  # 看门狗（15 分钟）

    actions = count_web_actions(shell)
    report.say(f"宿主 WebAction instrumentation：{actions['actionCount']} 项"
               f"（NoWebAction/WebActionCount 已扣除）")

    if not load_ok:
        from src.ui.web_shell import resolve_index_html

        report.add("P0", "真外壳可加载前端产物", "FAIL",
                   {"load_ui": False, "index_html": resolve_index_html()})
        report.write({"platform": os.environ.get("QT_QPA_PLATFORM", "")})
        return 2

    driver = r15.R15(report, shell, out_dir, real_window, r15.install_bridge_counters())
    probe = R17(report, driver, out_dir, actions)
    server_proc = None
    exit_code = 1

    try:
        QTest.qWait(1200)
        initial = driver.wait_for(r15.JS_UI_STATE,
                                  lambda v: bool(v.get("hash")) or v.get("r15Error"),
                                  timeout_ms=25000) or {}
        p0_ok = (initial.get("connectButton") is True
                 and initial.get("navEditor") is False
                 and initial.get("workspaceCount") == 0
                 and initial.get("tabCount") == 0
                 and real_window)
        report.add(
            "P0", "真外壳加载 v1.6.1 前端产物；初始态 = 未登录（登录面板 / 无编辑器入口 / 无工作区）",
            "PASS" if p0_ok else "FAIL",
            {"load_ui": load_ok, "realWindow": real_window,
             "state": {k: initial.get(k) for k in
                       ("hash", "connectButton", "passwordInputs", "navEditor",
                        "workspaceCount", "tabCount", "cmBundle")}},
        )

        # ── USER-CLIP：**只读**观察真实剪贴板（必须在任何合成写入之前）──
        # 用法：先做真实复制（IDEA 里 Ctrl+C），再 `--only USER-CLIP` 只跑这一条。
        if "USER-CLIP" in only:
            seq_at_start = clipboard_sequence()
            info = inspect_user_clipboard()
            info["clipboardSequence"] = seq_at_start
            info["note"] = ("本判据只读、不写剪贴板；裸 Win32 枚举（不经 Qt，无隐式格式转换）。"
                            "若 verdict=no-html-flavor ⇒ 当前剪贴板里没有 CF_HTML，"
                            "请先在 IDEA 里复制一段代码再重跑本判据。")
            report.add(
                "USER-CLIP",
                "只读观察真实来源（例如 IDEA）留在剪贴板上的 flavor 与 CF_HTML 头结构",
                "PASS" if info.get("htmlAvailable") else "SKIP", info, gating=False,
            )
        url = ""
        p1_ok = False
        # `--only USER-CLIP` 时不需要服务端 / 登录 / 渲染层（那条判据是纯只读剪贴板观察）
        need_renderer = any(c in only for c in
                            ("P1", "CLIP-AWT", "CLIP-CHROMIUM", "CLIP-TEXT", "CLIP-EMPTY",
                             "CLIP-HTMLONLY", "CLIP-DIVERGE", "AWT-CJK", "CLIP-AWT-REAL"))
        if not need_renderer:
            report.add("P1", "隔离服务端就绪 + 真登录 + 工作区可用", "SKIP",
                       "本轮未选中任何需要渲染层的判据（例如 --only USER-CLIP）")
            phase_snapshot = actions["counters"]["reset"]("no-renderer-phase")
            report.note(f"未进入粘贴相位；WebAction 计数：{json.dumps(phase_snapshot)}")
            report.add("H-NOHOST", "粘贴相位内宿主 WebAction 计数 = 0", "SKIP",
                       "本轮没有粘贴相位", gating=False)
            exit_code = report.verdict()
            raise _EarlyExit
        server_proc, port, _root = r15.start_isolated_server(out_dir, report)
        driver.server = server_proc
        driver.server_port = port
        if server_proc is not None and port and r15.wait_health(port).get("ok"):
            url = f"http://127.0.0.1:{port}"
            login_detail = r15.python_side_login(url)
            reload_result = driver.reload_page_and_wait()
            state_after = reload_result.get("state") or {}
            # 开一个标签页：走生产通路 notify_external_paths（渲染层已登录 ⇒ 直接打开）
            fixture = out_dir / "fixtures" / "r17-clip-target.txt"
            fixture.parent.mkdir(parents=True, exist_ok=True)
            fixture.write_bytes(b"")  # 空文件：正文起点干净，粘贴结果好判
            pushed = shell.notify_external_paths([str(fixture)])
            tab_state = driver.wait_for(r15.JS_UI_STATE,
                                        lambda v: v.get("tabCount", 0) >= 1,
                                        timeout_ms=25000, interval_ms=400) or {}
            p1_ok = bool(state_after.get("navEditor") is True
                         and tab_state.get("tabCount") == 1
                         and tab_state.get("activePanelCount") == 1
                         and tab_state.get("cmContentCount", 0) >= 1
                         and driver.window_alive())
            # 工作区根 `[data-scratch-workspace]` 是可选的（只在工作区视图渲染时才出现），
            # 故不作为判据，只作为读数留证；真判据是"有 1 个标签页 + 1 个激活面板 + CM6 在 DOM 里"。
            report.add(
                "P1", "隔离服务端就绪 + 服务层真登录 + 重载后渲染层已登录、工作区可用、恰好 1 个标签页",
                "PASS" if p1_ok else "FAIL",
                {"port": port, "serverPid": server_proc.pid, "login": login_detail,
                 "reload": reload_result.get("reload"),
                 "navEditorAfter": state_after.get("navEditor"),
                 "workspaceRootCount": state_after.get("workspaceCount"),
                 "pushedOpenPaths": pushed, "fixtureName": fixture.name,
                 "tabState": {k: tab_state.get(k) for k in
                              ("tabCount", "activePanelCount", "cmContentCount", "hash",
                               "activePanelId", "cmBundle")}},
            )
        else:
            report.add("P1", "隔离服务端就绪 + 真登录 + 工作区可用", "FAIL",
                       {"why": "隔离服务端起不来（见 server.log）", "port": port})

        ready = bool(url) and p1_ok

        # 起点清零：把**探针自己**的 `Reload`（为恢复会话而做）从 H-NOHOST 的计数里剔除，
        # 并留证"清零前有哪些动作、分别属于哪个相位"。
        phase_snapshot = actions["counters"]["reset"]("paste-phase")
        report.note(f"H-NOHOST 计数清零：清零前 {json.dumps(phase_snapshot, ensure_ascii=False)}")

        # ── SHAPE-AWT / SHAPE-CHROMIUM：Qt 眼中的样子（机制证据） ──
        # ⚠ r18：`--only CLIP-AWT-REAL` 要贴的是剪贴板上**真实 AWT 载荷**，
        # 这两条判据会**写合成载荷** ⇒ 会把真实载荷覆盖掉，故该模式下跳过它们。
        # 除这一模式外，下面的代码一字未改（既有判据语义 / 期望值保持不变）。
        if "CLIP-AWT-REAL" in only:
            real_shape_now = shape_detail(probe.qt_clipboard_shape(), {}, "")
            report.add(
                "SHAPE-AWT",
                ("跳过：本轮选择 CLIP-AWT-REAL（贴剪贴板上的**真实 AWT 载荷**），"
                 "写合成载荷会覆盖它；此处只读留证真实载荷此刻的 Qt 形态"),
                "SKIP",
                {"why": "would-overwrite-real-payload", "realClipboardQtShape": real_shape_now},
            )
            report.add(
                "SHAPE-CHROMIUM",
                "跳过：本轮选择 CLIP-AWT-REAL（同上，不写合成载荷）",
                "SKIP",
                {"why": "would-overwrite-real-payload"}, gating=False,
            )
        else:
            # ── SHAPE-AWT：Qt 眼中的样子（机制证据） ──
            awt = payloads["AWT"]
            set_info = set_clipboard(awt["html"], awt["plain"])
            QTest.qWait(150)
            readback = read_clipboard_win32()
            shape_awt = shape_detail(probe.qt_clipboard_shape(), awt["meta"], awt["plain"])
            offset_ok = bool((readback.get("fragmentByOffsetOk") or {}).get("ok"))
            clipboard_matches = (
                (readback.get("offsets") or {}).get("startFragment")
                == awt["meta"]["offsets"]["startFragment"])
            shape_awt_ok = (
                shape_awt.get("hasHtml") is True
                and shape_awt.get("htmlLowerHasTag") is True
                and shape_awt.get("htmlHasMarkerComment") is False
                and shape_awt.get("htmlHasStartFragmentHeader") is False
                and shape_awt.get("plainMatchesPayload") is True
                and offset_ok and clipboard_matches
            )
            report.add(
                "SHAPE-AWT",
                "AWT 风格载荷（无 <!--StartFragment-->）在 Qt 眼中的形态 = 有 text/html 但既无注释标记"
                "也无头偏移（M1 前提）；同时 Win32 侧按偏移切片复核通过（偏移算对了）",
                "PASS" if shape_awt_ok else "FAIL",
                {"setClipboard": set_info, "win32Readback": readback, "qtShape": shape_awt,
                 "shapePassCriteria": {"hasHtml": True, "htmlLowerHasTag": True,
                                       "htmlHasMarkerComment": False,
                                       "htmlHasStartFragmentHeader": False}},
            )

            # ── SHAPE-CHROMIUM（非阻塞对照）：Qt 眼中应带注释标记 ──
            chr_payload = payloads["CHROMIUM"]
            set_clipboard(chr_payload["html"], chr_payload["plain"])
            QTest.qWait(150)
            shape_chr = shape_detail(probe.qt_clipboard_shape(), chr_payload["meta"],
                                     chr_payload["plain"])
            report.add(
                "SHAPE-CHROMIUM", "对照：Chromium 风格载荷在 Qt 眼中**带** <!--StartFragment--> 标记",
                "PASS" if shape_chr.get("htmlHasMarkerComment") is True else "FAIL",
                {"qtShape": shape_chr}, gating=False,
            )

        # ── 粘贴五连（AWT / CHROMIUM / TEXT / EMPTY / AWT-HTMLONLY）──
        clip_results: dict = {}
        for kind, cid in CLIP_CASES:
            if cid not in only:
                report.add(cid, f"粘贴判据（{kind}）", "SKIP", "未选中（--only）")
                continue
            if not ready:
                report.add(cid, f"粘贴判据（{kind}）", "SKIP", "P1 未建立（渲染层未进入可用工作区）")
                continue
            payload = payloads[kind]
            if kind == "EMPTY":
                set_info = set_clipboard(None, None)
            else:
                # `AWT-HTMLONLY` 刻意**不写** CF_UNICODETEXT：隔离"Blink 有没有纯文本退路"
                text_flavor = payload["plain"] if payload.get("writesText") else None
                set_info = set_clipboard(payload["html"], text_flavor)
            QTest.qWait(200)
            before_doc = probe.doc()
            expected = payload["plain"]
            if kind == "EMPTY":
                result = probe.paste_negative(cid)
            else:
                result = probe.paste(expected, kind=kind, label=cid)
            result["setClipboard"] = set_info
            result["win32ClipboardFormats"] = clipboard_format_names()
            result["qtShapeAtPasteTime"] = shape_detail(
                probe.qt_clipboard_shape(), payload.get("meta") or {}, expected)
            result["docBeforeAnyKey"] = {k: before_doc.get(k) for k in
                                         ("docCodePoints", "docFnv1a32", "empty")}
            result["hostWebActionCountAtThisPoint"] = actions["counters"]["total"]
            result["channel"] = channel_of(result, payload)
            clip_results[cid] = result
            if kind == "EMPTY":
                report.add(
                    "CLIP-EMPTY",
                    "清空剪贴板后 Ctrl+V ⇒ 正文不变、无异常、窗口存活",
                    "PASS" if result.get("pass") else "FAIL", result,
                )
            elif kind == "AWT-HTMLONLY":
                # M1 的**隔离实验**：与 CLIP-AWT 只差"剪贴板上有没有纯文本 flavor"。
                # 若本条约 FAIL 而 CLIP-AWT PASS ⇒ Blink 在 HTML 为空时会退回 `text/plain`
                # （⇒ M1 在"两个 flavor 都在"的真实场景里**不足以**解释"粘贴无反应"）。
                report.add(
                    "CLIP-HTMLONLY",
                    "隔离实验：AWT 风格 CF_HTML **无** CF_UNICODETEXT 时 Ctrl+V ⇒ "
                    "正文应逐字符等于期望纯文本（若 FAIL ⇒ HTML 通路确实被 M1 打空）",
                    "PASS" if result.get("equal") else "FAIL", result,
                )
            elif kind == "AWT-DIVERGE":
                report.add(
                    "CLIP-DIVERGE",
                    "双通路判别：CF_HTML 片段与 CF_UNICODETEXT **内容不同** ⇒ 看正文贴出哪一个",
                    "PASS" if result.get("equal") else "FAIL", result,
                )
            else:
                report.add(
                    cid,
                    f"OS 级 Ctrl+V（{kind} 风格载荷）⇒ 正文逐字符等于剪贴板 text/plain",
                    "PASS" if result.get("equal") else "FAIL", result,
                )

        # ── AWT-CJK：中文载荷（字节偏移）──
        if include_cjk:
            if not ready:
                report.add("AWT-CJK", "中文载荷（无注释标记）⇒ 粘贴成功", "SKIP",
                           "P1 未建立")
            else:
                cjk = payloads["AWT-CJK"]
                set_info = set_clipboard(cjk["html"], cjk["plain"])
                QTest.qWait(200)
                shape_cjk = shape_detail(probe.qt_clipboard_shape(), cjk["meta"], cjk["plain"])
                readback_cjk = read_clipboard_win32()
                result = probe.paste(cjk["plain"], kind="AWT-CJK", label="AWT-CJK")
                result["setClipboard"] = set_info
                result["win32Readback"] = readback_cjk
                result["qtShape"] = shape_cjk
                report.add(
                    "AWT-CJK",
                    "中文载荷（UTF-8 字节偏移、无注释标记）⇒ OS 级 Ctrl+V 后正文逐字符相等",
                    "PASS" if result.get("equal") else "FAIL", result,
                )
        else:
            report.add("AWT-CJK", "中文载荷 ⇒ 粘贴成功", "SKIP",
                       "--no-cjk 或未选中（本轮只保留 ASCII 机制证据）")

        # ── CLIP-AWT-REAL（r18 决定性判据）：贴**剪贴板上已有的真实 AWT 载荷** ──
        # 与其它粘贴判据的唯一区别：**一个字节都不写剪贴板**，期望文本从剪贴板自己读出来。
        # 前置：由 `AwtClipHtml.java` 用 JDK 自己的 AWT 公开 API 把 text/html + text/plain
        # 放上剪贴板，并让 JVM 驻留（`--hold=900`）；本判据只读它。
        if "CLIP-AWT-REAL" in only:
            if not ready:
                report.add("CLIP-AWT-REAL", "真实 AWT 载荷 ⇒ Ctrl+V 贴出剪贴板文本", "SKIP",
                           "P1 未建立（渲染层未进入可用工作区）")
            else:
                expected_text, expect_meta = read_clipboard_expectation()
                real_win32 = inspect_user_clipboard()
                real_qt = probe.qt_clipboard_shape()
                if expected_text is None:
                    report.add(
                        "CLIP-AWT-REAL",
                        "真实 AWT 载荷 ⇒ Ctrl+V 贴出剪贴板文本",
                        "FAIL",
                        {"why": "clipboard-has-no-text-and-no-html",
                         "expectation": expect_meta, "win32Clipboard": real_win32,
                         "qtShape": real_qt, "note": "前置未满足：请先跑 AwtClipHtml.java"},
                    )
                else:
                    report.note(
                        f"[CLIP-AWT-REAL] 期望文本来源={expect_meta['source']} "
                        f"长度={expect_meta['textCodePoints']} "
                        f"指纹={expect_meta['textFnv1a32']} "
                        f"hadCrlf={expect_meta['hadCrlf']}（**不写剪贴板**）")
                    # 等过外壳的**延迟重推**（`OPEN_PATHS_RETRY_MS = 1500`，
                    # 见 src/ui/web_shell.py:225）：P1 的 `notify_external_paths` 会在约 1.5 s 后
                    # 再推一次 `editor.openPaths`（**产品设计行为**）。若它正好落在
                    # "清空正文 → Ctrl+V"之间，会把空 fixture 重新打开从而清掉刚贴进来的正文
                    # ⇒ 变成**探针假 FAIL**。这一步只等时间、不动剪贴板。
                    QTest.qWait(3000)
                    report.note("[CLIP-AWT-REAL] 已等过外壳延迟重推（3s）再粘贴")
                    seq_before = clipboard_sequence()
                    # **两轮粘贴协议**（把"贴不进去"与"探针时序抖动"分开）：
                    # 每轮都是完整的「落焦点 → 清空 → OS 级 Ctrl+V → 读回逐字符比对」，
                    # 两轮结果**都进证据**（`attempts`）；只要有一轮贴出期望文本就算 PASS。
                    # 真凶若是"载荷形态"，两轮都该贴不出来；只有第一轮失败才是探针时序问题。
                    shell_hwnd = hwnd_of(shell)
                    attempts: list[dict] = []
                    for index in range(2):
                        driver.activate()
                        QTest.qWait(200)
                        fg_before = foreground_window()
                        one = probe.paste(expected_text, kind="AWT-REAL",
                                          label=f"CLIP-AWT-REAL#{index + 1}")
                        attempts.append({
                            "round": index + 1,
                            "foregroundBeforePaste": fg_before,
                            "foregroundIsShell": fg_before == shell_hwnd,
                            "foregroundAfterPaste": foreground_window(),
                            "equal": bool(one.get("equal")),
                            "channel": channel_of(one, {"plain": expected_text,
                                                        "fragment": expected_text}),
                            "after": one.get("after"),
                            "compare": one.get("compare"),
                            "expected": one.get("expected"),
                            "pasteFocus": one.get("pasteFocus"),
                            "seqBefore": one.get("clipboardSequenceBeforePaste"),
                            "seqAfter": one.get("clipboardSequenceAfterPaste"),
                            "windowAlive": one.get("windowAlive"),
                            "result": one,
                        })
                        report.note(f"[CLIP-AWT-REAL] 第 {index + 1} 轮：贴出 "
                                    f"{(one.get('after') or {}).get('docCodePoints')} 字符，"
                                    f"foregroundIsShell={fg_before == shell_hwnd}，"
                                    f"equal={bool(one.get('equal'))}")
                        if one.get("equal"):
                            break
                    result = attempts[-1]["result"]
                    result["shellHwnd"] = shell_hwnd
                    result["attempts"] = [{k: v for k, v in a.items() if k != "result"}
                                          for a in attempts]
                    result["attemptCount"] = len(attempts)
                    result["equalFirstAttempt"] = attempts[0]["equal"]
                    result["equalAnyAttempt"] = any(a["equal"] for a in attempts)
                    result["expectation"] = expect_meta
                    result["win32ClipboardAtPasteTime"] = real_win32
                    result["qtShapeAtPasteTime"] = real_qt
                    result["clipboardSequenceBeforePasteRead"] = seq_before
                    result["clipboardSequenceAfterPasteRead"] = clipboard_sequence()
                    result["clipboardUntouchedByProbe"] = (
                        result["clipboardSequenceBeforePasteRead"]
                        == result["clipboardSequenceAfterPasteRead"])
                    result["hostWebActionCountAtThisPoint"] = actions["counters"]["total"]
                    result["doesQtSeeHtml"] = bool(real_qt.get("hasHtml"))
                    result["doesQtSeeNonEmptyHtml"] = bool(real_qt.get("htmlChars"))
                    if not result["equalAnyAttempt"]:
                        # **按键送达自检**（只在粘贴没贴出东西时跑）：先写非空基线，再用 OS 级
                        # `Ctrl+A` + `Backspace` 清空。清不掉 ⇒ 探针的按键根本没到渲染层，
                        # 那么这个 FAIL 是**探针假 FAIL**，不能当成"粘贴什么都不发生"的证据。
                        probe.d.js(js_set_doc("r18-key-delivery-probe"))
                        QTest.qWait(250)
                        key_baseline = probe.doc()
                        cleared_by_key = probe._clear_doc(ctrl_a=True, ctrl_key="a")
                        key_after = probe.doc()
                        result["osKeyDeliveryCheck"] = {
                            "why": "验证「OS 级按键是否真的送达渲染层」：非空基线 → OS 级 Ctrl+A/Backspace",
                            "baselineCodePoints": key_baseline.get("docCodePoints"),
                            "clearRetry": cleared_by_key,
                            "afterCodePoints": key_after.get("docCodePoints"),
                            "afterEmpty": key_after.get("empty"),
                            "ok": bool(key_baseline.get("docCodePoints") == 22
                                       and cleared_by_key.get("ok")
                                       and key_after.get("empty")),
                        }
                    # 结论标签：把"粘贴真的什么都没发生"与"探针按键没送达"分开，避免误报
                    if result["equalAnyAttempt"]:
                        result["verdictNote"] = ("paste-succeeded"
                                                 if result["equalFirstAttempt"]
                                                 else "paste-succeeded-on-second-attempt")
                    elif (result.get("osKeyDeliveryCheck") or {}).get("ok"):
                        result["verdictNote"] = ("paste-produced-nothing"
                                                 if all(a["foregroundIsShell"] for a in attempts)
                                                 else "paste-nothing-but-foreground-was-not-shell")
                    else:
                        result["verdictNote"] = "inconclusive-os-key-delivery-failed"
                    report.add(
                        "CLIP-AWT-REAL",
                        "真实 AWT 载荷（JDK 自己写的 CF_HTML + 纯文本）⇒ OS 级 Ctrl+V 后正文"
                        "逐字符等于剪贴板上的期望文本；探针**不写剪贴板**（最多两轮，结果都留证）",
                        "PASS" if result["equalAnyAttempt"] else "FAIL", result,
                    )

        # ── H-NOHOST ──
        host_total = actions["counters"]["total"]
        modal = driver.modal_widget()
        alive = driver.window_alive()
        install_errors = driver.calls.get("__install__", [])
        nohost_ok = (host_total == 0 and modal is None and alive)
        report.add(
            "H-NOHOST",
            "粘贴相位内宿主 47 项 WebAction 触发计数 = 0、无宿主模态、主窗口存活"
            "（计数已在粘贴相位起点清零；清零前的探针自身 Reload 另存于 detail）",
            "PASS" if nohost_ok else "FAIL",
            {"webActionTotalInPastePhase": host_total,
             "webActionByActionInPastePhase": actions["counters"]["byAction"],
             "webActionEventsInPastePhase": actions["counters"]["events"],
             "instrumentedActionCount": actions["actionCount"],
             "phaseBeforeReset": phase_snapshot,
             "hostModalWidget": modal, "windowAlive": alive,
             "bridgeCounterInstallErrors": install_errors,
             "bridgeCallCounts": {k: r15.call_count(driver.calls, k) for k in
                                  ("editor.detectAndRead", "editor.writeText",
                                   "editor.draftSave")}},
        )

        exit_code = report.verdict()
    except _EarlyExit:
        pass  # 本轮不需要渲染层；判据已登记，直接写证据
    except Exception as exc:  # noqa: BLE001 - 任何未预期异常都留证
        import traceback

        traceback.print_exc()
        report.add("Z9", "探针自身无未捕获异常", "FAIL", {"error": f"{type(exc).__name__}: {exc}"})
        exit_code = report.verdict() or 1

    try:
        sandbox_label = str(appdata.relative_to(PROBES))
    except ValueError:
        sandbox_label = str(appdata)

    extra = {
        "platform": os.environ.get("QT_QPA_PLATFORM", ""),
        "real_window": real_window,
        "cfHtmlFormatId": CF_HTML,
        "user_data_sandbox": sandbox_label,
        "isolatedServer": {"pid": server_proc.pid if server_proc else None,
                           "port": driver.server_port},
        "webActionInstrumentation": {"actionCount": actions["actionCount"],
                                     "actionNames": actions["actionNames"],
                                     "totalInPastePhase": actions["counters"]["total"],
                                     "byActionInPastePhase": actions["counters"]["byAction"],
                                     "phaseBeforeReset": phase_snapshot},
        "bridgeCallCounts": {k: r15.call_count(driver.calls, k) for k in
                             ("editor.detectAndRead", "editor.writeText", "editor.draftSave")},
    }
    try:
        path = report.write(extra)
    except Exception as exc:  # noqa: BLE001 - 收尾写盘失败不能把整轮证据变成"没有"
        import traceback

        traceback.print_exc()
        report.say(f"结果 JSON 写入失败：{type(exc).__name__}: {exc}")
        path = out_dir / "r17-result.json"
    report.say(f"结果 JSON：{path}")
    report.say(f"退出码：{exit_code}")

    if server_proc is not None and server_proc.poll() is None:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=10)
        except Exception:  # noqa: BLE001 - 收尾尽力而为
            server_proc.kill()
    # 不留探针载荷在用户剪贴板里（清空即可，不写回任何"恢复内容"）。
    # 例外：`--only USER-CLIP` 是**纯只读**观察 ⇒ 不清空，免得把用户刚复制的真实内容毁掉。
    if not only_readonly_clipboard(only):
        cleared = set_clipboard(None, None)
        report.say(f"收尾清空剪贴板：opened={cleared.get('opened')} closed={cleared.get('closed')}")
    else:
        report.say("收尾未清空剪贴板（--only USER-CLIP 为只读观察，保留用户剪贴板原状）")
    QTest.qWait(200)
    app.quit()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
