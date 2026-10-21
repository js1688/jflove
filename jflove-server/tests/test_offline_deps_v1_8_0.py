"""
离线下载依赖/镜像静态门禁（v1.8.0 / S8）

只做静态断言（镜像构建耗时且需网络）：
  - 解析组件版本必须固定在 requirements.txt（I-6：禁止运行时在线自更新）；
  - Dockerfile 必须有 WITH_BROWSER 开关 + 构建期 Chromium 启动校验 + ffprobe 校验；
  - 缺浏览器时必须响亮报警（不得静默）。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_requirements_pin_parser_dependencies() -> None:
    """yt-dlp / httpx / playwright 必须钉死版本。"""
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "yt-dlp==2026.8.19" in text
    assert "httpx==0.28.1" in text
    assert "playwright==1.64.0" in text


def test_dockerfile_has_browser_switch_and_loud_failure() -> None:
    """WITH_BROWSER 开关、构建期启动校验、缺省报警都必须存在。"""
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG WITH_BROWSER=1" in text
    assert "playwright install --with-deps chromium" in text
    assert "chromium.launch(headless=True" in text
    assert "[FATAL] Chromium 启动校验失败" in text
    assert "[WARN] WITH_BROWSER=0" in text
    assert "ffprobe -version" in text
    assert "yt_dlp" in text


def test_main_registers_offline_router_and_workers() -> None:
    """路由与 worker 必须在 lifespan 内注册/启停（不能只写 controller）。"""
    text = (ROOT / "src" / "main.py").read_text(encoding="utf-8")
    assert "offline_download_controller.router" in text
    assert "offline_download_service.start_workers" in text
    assert "offline_download_service.stop_workers" in text
