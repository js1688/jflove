"""
文件关联服务模块（v1.6.0 文本编辑器 · 设计 §6.7 / §7.1 #14 #15 #16 / AC-43 / AC-44）

职责：把"JFLove 声明支持哪些扩展名"这件事收敛成**一条派生链**，并读写应用内的勾选偏好：

    ui/src/config/scratch-types.ts  （唯一手写真相）
        └─ node scripts/gen_file_assoc_manifest.mjs
             └─ ui/public/file-assoc.manifest.json  （生成物；随 dist/ 进包 → webui/）
                  ├─ 本模块（supported / set 校验 / osRegistered 判定）
                  └─ scripts/gen_file_assoc_assets.py → jflove.xml / .desktop / file-assoc.iss

对应桥方法（`bridge.py` 的白名单，§7.1）：

    file_assoc.get                 → :func:`get`
    file_assoc.set                 → :func:`set`
    file_assoc.openSystemDefaults  → :func:`open_system_defaults`（**GUI 主线程**）

关键契约

1. **manifest 缺失不抛**：`supported: []` + ``note`` 说明原因（§7.1 #14 的错误语义）。
   扩展名清单**绝不**在本文件里手打（§5.1 反例禁止）——本模块只读 manifest。
2. **`set` 遇不支持的扩展名抛错，不静默过滤**（§7.1 #15）：静默丢弃会让用户在设置页看到的
   勾选与 `file_assoc.json` 不一致，是"看起来成功"的假象。
3. **落盘位置**：`settings.FILE_ASSOC_FILE`（= `USER_DATA_DIR/storage/file_assoc.json`，
   §4.3）；结构 `{"version":1,"selected":["log","json"]}`。写盘走
   :func:`editor_service.atomic_write`（同目录 ``mkstemp`` 随机名 + ``os.replace``，
   §6.2.1），**不用**固定 ``.tmp`` 名（那是本版本要修的既有缺陷）。
4. **冻结态 / 开发态的 manifest 路径**（§6.7.1）：
   - 冻结态（PyInstaller）读 ``resolve_web_root()/file-assoc.manifest.json``
     （即解包目录下的 ``webui/``，由 build.py 的 ``--add-data`` 提供）；
   - 开发态读 ``jflove-desktop/ui/public/file-assoc.manifest.json``。
   ``resolve_web_root()`` 从 :mod:`src.ui.web_shell` **借用**（不在本模块另造一套路径规则）；
   因为它会拉起 QtWebEngine，故**只在冻结态**才 import。
5. **§6.7.4 的语义边界（必须写清，避免误期望）**：应用内勾选**不修改系统默认程序**。
   安装包的系统注册是**构建期静态生成**的；运行时勾选只决定 JFLove 自己声明支持哪些扩展名，
   可观测效果 = ① 跨重启保留（AC-44）；② 应用内「用 JFLove 打开」的候选过滤 / 提示文案；
   ③ 本模块 :func:`get` 的返回值。改系统默认程序只能由用户在系统设置里自己动手
   （:func:`open_system_defaults` 只是把它打开，不代替用户点确认）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from src.config import settings
from src.services.editor_service import atomic_write
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: manifest 文件名（§5.1；与 mjs 生成器、gen_file_assoc_assets.py 三处一致）
MANIFEST_NAME = "file-assoc.manifest.json"
#: 勾选偏好的结构版本（§4.3：`{"version":1,"selected":[...]}`）
CONFIG_VERSION = 1
#: 平台字段取值（§7.1 #14 的 `platform`）
PLATFORM_WIN32 = "win32"
PLATFORM_LINUX = "linux"
PLATFORM_OTHER = "other"

#: Windows 注册位置（§6.7.2：`HKCU\Software\Classes\Applications\JFLove.exe`）。
#: 由 :func:`_is_os_registered` **只读**打开；本模块**没有任何写注册表的代码路径**。
_WINDOWS_APP_KEY = rf"Software\Classes\Applications\{settings.APP_NAME}.exe"
#: Linux 上 RPM 的登记位置（§6.7.3：MIME 包 + .desktop），只用于只读判定
_LINUX_MIME_PATHS = (
    "/usr/share/mime/packages/jflove.xml",
    "/usr/local/share/mime/packages/jflove.xml",
)
_LINUX_DESKTOP_PATHS = (
    "/usr/share/applications/jflove.desktop",
    "/usr/local/share/applications/jflove.desktop",
)

#: 无论清单是否可用都成立的语义说明（§6.7.4 的边界，直接展示给用户）
_SEMANTIC_NOTE = (
    "应用内勾选只声明 JFLove 自己支持哪些扩展名，不会修改系统默认程序；"
    "要改默认程序请在系统「默认应用」设置里自行选择。"
)


# ── 清单（manifest）读取 ────────────────────────────────────────────

def _manifest_path() -> Path:
    """
    解析 manifest 的绝对路径（冻结态 / 开发态两条规则，§6.7.1）。

    :returns: manifest 文件路径（**不保证存在**）
    """
    if getattr(sys, "frozen", False):
        # 延迟 import：resolve_web_root 所在的 web_shell 会拉起 QtWebEngine，
        # 开发态读 ui/public 时没必要付这个代价（且便于 headless 单测）。
        from src.ui.web_shell import resolve_web_root

        return Path(resolve_web_root()) / MANIFEST_NAME
    # 本文件在 src/services/ 下 → 连退三级到 jflove-desktop/
    desktop_root = Path(__file__).resolve().parents[2]
    return desktop_root / "ui" / "public" / MANIFEST_NAME


def load_supported() -> tuple[list[str], str]:
    """
    读出**清单派生的**扩展名列表（顺序 = manifest 顺序）。

    **永不抛异常**：manifest 缺失 / 非法 JSON / 结构不对 ⇒ 空列表 + 可读原因，
    由调用方放进 ``note``（§7.1 #14 明确要求"不抛"）。

    :returns: `(扩展名列表, 说明文字)`；说明文字为 `""` 表示清单正常
    """
    path = _manifest_path()
    if not path.is_file():
        logger.warning("文件关联清单缺失，supported 按空处理（不抛）: %s", path.name)
        return [], (
            f"未找到文件关联清单 {MANIFEST_NAME}：暂时无法列出可关联的扩展名。"
            "请重新构建前端（生成脚本会产出该清单）。"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        logger.warning("文件关联清单读取/解析失败，supported 按空处理（不抛）：%s", type(exc).__name__)
        return [], f"文件关联清单无法解析（{type(exc).__name__}）：请重新运行清单生成脚本。"

    types = raw.get("types") if isinstance(raw, dict) else None
    if not isinstance(types, list) or not types:
        logger.warning("文件关联清单结构异常，supported 按空处理（不抛）")
        return [], "文件关联清单结构异常（缺少非空的 types 数组）：请重新运行清单生成脚本。"

    exts: list[str] = []
    for entry in types:
        if not isinstance(entry, dict):
            continue
        ext = normalize_ext(entry.get("ext"))
        if ext and ext not in exts:
            exts.append(ext)
    if not exts:
        return [], "文件关联清单里没有任何可用扩展名：请重新运行清单生成脚本。"
    return exts, ""


def normalize_ext(raw: Any) -> str:
    """
    归一化扩展名：去空白、去前导点、转小写（与渲染层 `normalizeExtension` 同语义）。

    :param raw: 任意用户 / manifest 输入（非字符串按空处理）
    :returns: 归一化后的扩展名（不可用时为 `""`）
    """
    if not isinstance(raw, str):
        return ""
    return raw.strip().lstrip(".").lower()


# ── 勾选偏好读写（`settings.FILE_ASSOC_FILE`） ──────────────────────

def _config_path() -> str:
    """取勾选偏好文件路径（**每次调用都读 settings**，便于测试注入临时目录）。"""
    return os.path.abspath(str(settings.FILE_ASSOC_FILE))


def _read_selected() -> tuple[list[str], str]:
    """
    读勾选偏好（**永不抛异常**）。

    :returns: `(已勾选扩展名列表, 说明文字)`；说明文字非空表示文件缺失 / 损坏 / 被忽略的条目
    """
    path = _config_path()
    if not os.path.exists(path):
        # 默认全不勾选（D22）：首次运行不写文件，也不改变任何系统状态
        return [], ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        logger.warning("勾选配置无法读取，按全不勾选处理（不抛）：%s", type(exc).__name__)
        return [], "文件关联勾选配置损坏，已按「全不勾选」处理（不影响系统默认程序）。"

    if not isinstance(raw, dict) or raw.get("version") != CONFIG_VERSION:
        return [], "文件关联勾选配置版本无法识别，已按「全不勾选」处理。"
    items = raw.get("selected")
    if not isinstance(items, list):
        return [], "文件关联勾选配置缺少 selected 数组，已按「全不勾选」处理。"

    selected: list[str] = []
    for item in items:
        ext = normalize_ext(item)
        if ext and ext not in selected:
            selected.append(ext)
    return selected, ""


def _write_selected(selected: list[str]) -> None:
    """
    **原子**写入勾选偏好（同目录临时文件 + `os.replace`，§6.2.1）。

    :param selected: 已归一化、已去重、已校验的扩展名列表
    :raises OSError: 写盘失败（原文件保持旧内容）
    """
    path = _config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"version": CONFIG_VERSION, "selected": list(selected)}
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_write(path, body.encode("utf-8"))


# ── 系统登记状态（只读判定，**绝不写注册表**） ───────────────────────

def _platform() -> str:
    """映射 `sys.platform` → §7.1 #14 的 `platform` 取值（`win32` / `linux` / `other`）。"""
    if sys.platform == "win32":
        return PLATFORM_WIN32
    if sys.platform.startswith("linux"):
        return PLATFORM_LINUX
    return PLATFORM_OTHER


def _is_os_registered(platform: str) -> bool:
    """
    只读判断"系统里是否已有 JFLove 的关联登记"（供设置页展示，§4.3 第 2 块）。

    - Windows：`HKCU\\Software\\Classes\\Applications\\JFLove.exe` 是否存在
      （只 `OpenKey` + `KEY_READ`，**不创建、不写值**）；
    - Linux：`share/mime/packages/jflove.xml` 或 `share/applications/jflove.desktop`
      是否在位（RPM 安装位置，§6.7.3）；
    - 其它平台：`False`。

    :param platform: :func:`_platform` 的返回值
    :returns: 是否已登记
    """
    if platform == PLATFORM_WIN32:
        try:
            import winreg  # noqa: PLC0415 —— 只读判定用，非 Windows 不 import
        except ImportError:  # pragma: no cover - 非 Windows 上不可达
            return False
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WINDOWS_APP_KEY, 0, winreg.KEY_READ):
                return True
        except OSError:
            return False
    if platform == PLATFORM_LINUX:
        home = os.path.expanduser("~")
        candidates = list(_LINUX_MIME_PATHS) + list(_LINUX_DESKTOP_PATHS) + [
            os.path.join(home, ".local", "share", "mime", "packages", "jflove.xml"),
            os.path.join(home, ".local", "share", "applications", "jflove.desktop"),
        ]
        return any(os.path.exists(path) for path in candidates)
    return False


# ── 对外 API（对应桥方法 §7.1 #14 / #15 / #16） ──────────────────────

def get() -> dict[str, Any]:
    """
    读取文件关联状态（对应 `file_assoc.get`，§7.1 #14 / AC-44）。

    **永不抛异常**：manifest 缺失 ⇒ `supported: []` + `note` 说明原因。

    :returns: `{"selected": [str], "supported": [str], "platform": str,
              "osRegistered": bool, "note": str}`
    """
    supported, manifest_note = load_supported()
    stored, config_note = _read_selected()
    # 只报"清单里仍然支持"的勾选：升级后清单缩短时，陈旧条目不进 supported 也不会
    # 让渲染层渲染出清单外的勾选项（**注意**：这只是读侧收口，写入侧仍然抛错不静默过滤）
    selected = [ext for ext in stored if ext in supported]
    dropped = [ext for ext in stored if ext not in supported]

    notes = [item for item in (manifest_note, config_note) if item]
    if dropped:
        notes.append(f"有 {len(dropped)} 个已勾选扩展名已不在当前支持清单里，已忽略（请重新勾选）。")
    platform = _platform()
    notes.append(_SEMANTIC_NOTE)
    return {
        "selected": selected,
        "supported": supported,
        "platform": platform,
        "osRegistered": _is_os_registered(platform),
        "note": " ".join(notes),
    }


def set(selected: Any) -> dict[str, Any]:  # noqa: A001 —— 桥方法名就叫 file_assoc.set
    """
    保存勾选偏好（对应 `file_assoc.set`，§7.1 #15 / AC-44）。

    - 归一去重后**再校验**：出现不支持的扩展名 ⇒ 抛 :class:`ValueError`，
      **不静默过滤**（§7.1 #15 的明文要求），且此时**不写盘**；
    - 只写 `settings.FILE_ASSOC_FILE`，**不碰系统注册表 / 不调 xdg-mime**（§6.7.4 边界）。

    :param selected: 渲染层传来的扩展名数组（可带点、可大小写混合；非字符串元素非法）
    :returns: `{"ok": True, "selected": [归一化后的扩展名]}`
    :raises ValueError: 入参不是数组 / 含非字符串 / 含不支持的扩展名
    """
    if not isinstance(selected, (list, tuple)):
        raise ValueError("file_assoc.set 的 selected 必须是字符串数组")

    supported, manifest_note = load_supported()
    if manifest_note:
        raise ValueError(f"无法保存文件关联勾选：{manifest_note}")

    normalized: list[str] = []
    for item in selected:
        if not isinstance(item, str):
            raise ValueError("file_assoc.set 的 selected 只能包含字符串")
        ext = normalize_ext(item)
        if ext and ext not in normalized:
            normalized.append(ext)
    unsupported = [ext for ext in normalized if ext not in supported]
    if unsupported:
        raise ValueError(
            "不支持的扩展名（未静默过滤）：" + ", ".join(unsupported)
            + "；可选范围见 file_assoc.get 的 supported"
        )

    _write_selected(normalized)
    logger.info("文件关联勾选已保存：%d 项（%s）", len(normalized), ", ".join(normalized) or "无")
    return {"ok": True, "selected": normalized}


def open_system_defaults() -> dict[str, Any]:
    """
    打开系统的「默认应用」设置（对应 `file_assoc.openSystemDefaults`，§7.1 #16）。

    注册到 **GUI 主线程**调用（`QDesktopServices.openUrl` 需要 GUI 线程）。
    **只打开设置页，不代替用户确认**——本方法不会写任何关联 / 默认程序信息。

    :returns: `{"ok": bool, "hint": str}`；平台不支持 ⇒ `ok=False` + 指引
    """
    platform = _platform()
    if platform == PLATFORM_WIN32:
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
        except ImportError:  # pragma: no cover - 依赖缺失时降级为提示，不抛
            return {"ok": False, "hint": "无法打开系统设置（Qt 组件不可用）；请在「设置 → 应用 → 默认应用」中手动选择。"}
        opened = bool(QDesktopServices.openUrl(QUrl("ms-settings:defaultapps")))
        return {
            "ok": opened,
            "hint": (
                "已打开系统「默认应用」设置。JFLove 只出现在各扩展名的「选择其他应用」候选里；"
                "把默认程序改成 JFLove 需要你自己在此确认（应用内勾选不会改默认程序）。"
            ),
        }
    if platform == PLATFORM_LINUX:
        return {
            "ok": False,
            "hint": (
                "Linux 请在桌面环境的「默认应用」设置里选择 JFLove"
                "（GNOME：设置 → 默认应用）。本应用不调用 xdg-mime default，也不写 mimeapps.list。"
            ),
        }
    return {"ok": False, "hint": "当前平台不支持自动打开默认应用设置，请手动在系统设置中选择。"}
