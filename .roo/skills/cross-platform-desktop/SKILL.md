---
name: cross-platform-desktop
description: 负责 jflove-desktop 跨平台桌面应用开发，支持 Windows、Linux、macOS。当用户要求开发桌面客户端、桌面应用时使用此技能。
---

# cross-platform-desktop

- 资深桌面应用工程师，专注于 jflove-desktop 模块。
- 严格遵守最新版设计文档与后端开发记录，前后端保持一致。
- 注重现代极简 UI 审美，兼顾跨平台兼容性。
- 通用编码规范：见 AGENTS.md §4（命名）、§1（禁止越界）。

## 技术栈（模块特有）

- 桌面框架：PySide6 6.8.0.2；组件库：PySide6-Fluent-Widgets；UI 规范：Material Design
- 状态管理：Redux 模式（基于信号槽）；HTTP：全部走 `src/utils/http_client.py`
- 加密/测试/风格/构建/日志：与后端一致（见 AGENTS.md §2）

## 行为规范

- MVC 架构：`components/` / `services/` / `utils/` / `config/` / `ui/`，各层归位。
- `services/` 封装所有后端调用，UI 层禁止直接发起 HTTP。
- 跨平台特定逻辑（路径、字体、托盘）必须做平台判断。
- 耗时操作用多线程，避免阻塞 UI 线程；全局异常 + 友好提示。
- 版本迭代时必须同步更新 `src/config/settings.py` 的 `APP_VERSION`，UI 页禁止硬编码版本号。

> **版本迭代前置**：开始前阅读上一版审查报告和测试报告，将严重/中等缺陷列为本版**必须修复**项，轻微酌情。开发记录中逐条注明处理方式。

## 文档更新范围

- 路径：`文档记录/桌面端开发记录/<版本号>.md`；同步更新 `jflove-desktop/README.md`
- 必须包含：功能与改动点、页面/组件/服务方法、调用的后端接口、逻辑对比、设计取舍

## 安全宪法（参见 AGENTS.md §9）

- **HTTP**：禁止在 `services/`/`ui/`/`components/` 直接 `import requests`，全部走 `http_client`
- **下载/预览**：用 `http_client.download_to_file` / `download_stream`，内部已按帧解密
- **敏感信息**：session_key/JWT/密码禁止写日志、写 QSettings、写文件。`session_manager` 持久化字段已限定
- **路径**：`os.path.normpath` + 防目录遍历；用户输入不拼接进 URL（业务参数走加密 body）
- **文件元数据**：文件名/大小/路径必须从已解密响应取值，禁止从 HTTP 响应头读
