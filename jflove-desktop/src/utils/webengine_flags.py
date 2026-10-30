"""
QtWebEngine 启动参数（**必须在创建 QApplication 之前生效**）。

## 为什么要关掉 Windows 的 native occlusion

Chromium 在 Windows 上有一套「窗口被遮挡/最小化 ⇒ 停止出帧」的省电优化
（`CalculateNativeWinOcclusion`）。最小化后窗口被判定为 occluded，渲染进程停止合成；
而恢复窗口时，如果外壳没有显式唤醒渲染器（见 `WebShell._wake_renderer`），
就可能出现**DOM 正常、整片空白**的观感（用户实测：最小化再打开，整个面板空白）。

两侧一起做：
1. 这里关掉该特性 —— 从源头避免"被判定为不可见就不出帧"；
2. 外壳在恢复边沿显式补一次唤醒/重绘 —— 即使平台仍判定遮挡也能自愈。

代价只是被遮挡时仍可能出帧（CPU 略增），换「恢复必有画面」。

## 为什么是"合并"而不是"覆盖"

`QTWEBENGINE_CHROMIUM_FLAGS` 可能已经被用户/发行版设置过（例如显卡兼容参数）。
直接覆盖会把用户参数吞掉，所以这里做**token 级合并**：
已有 `--disable-features=A,B` 就追加到同一个开关里（Chromium 只认最后一个
`--disable-features`，另写一个会把前面的特性开关覆盖掉）。
"""

from __future__ import annotations

import os
import sys

#: 环境变量名（QtWebEngine 读取）
FLAGS_ENV = "QTWEBENGINE_CHROMIUM_FLAGS"

#: 要关闭的 Chromium 特性（Windows 原生遮挡判定）
OCCLUSION_FEATURE = "CalculateNativeWinOcclusion"

#: 需要注入该特性的平台（sys.platform 取值）
WINDOWS_PLATFORM = "win32"


def build_chromium_flags(
    existing: str | None,
    platform: str | None = None,
) -> str:
    """
    在已有 flags 上合并「关闭 Windows 原生遮挡节流」。

    :param existing: 当前 `QTWEBENGINE_CHROMIUM_FLAGS` 的值（可为 None/空）
    :param platform: 平台标识，默认 `sys.platform`（便于单测注入）
    :returns: 合并后的 flags（空格分隔）；非 Windows 原样返回
    """
    base = (existing or "").strip()
    if (platform or sys.platform) != WINDOWS_PLATFORM:
        return base
    if OCCLUSION_FEATURE in base:
        # 已包含（无论是 disable 还是 enable，尊重既有配置，不重复注入）
        return base

    tokens = base.split()
    for index, token in enumerate(tokens):
        if token.startswith("--disable-features="):
            features = token.split("=", 1)[1].strip()
            if not features:
                tokens[index] = f"--disable-features={OCCLUSION_FEATURE}"
            else:
                tokens[index] = f"{token},{OCCLUSION_FEATURE}"
            return " ".join(tokens)

    tokens.append(f"--disable-features={OCCLUSION_FEATURE}")
    return " ".join(tokens)


def apply_chromium_flags(platform: str | None = None) -> str:
    """
    把合并结果写回环境变量（幂等；应在 `QApplication` 之前调用）。

    :param platform: 平台标识，默认 `sys.platform`
    :returns: 合并后的 flags 字符串
    """
    merged = build_chromium_flags(os.environ.get(FLAGS_ENV), platform)
    if merged:
        os.environ[FLAGS_ENV] = merged
    return merged
