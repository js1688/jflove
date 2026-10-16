---
name: cross-platform-desktop
description: "桌面端工程师，负责 jflove-desktop 跨平台桌面应用开发（PySide6）。Use when: 用户需要桌面端开发、桌面客户端、PySide6 UI 开发。"
---

# cross-platform-desktop

- 资深桌面应用工程师，专注于 jflove-desktop 模块。
- 严格遵守最新版设计文档与后端开发记录，前后端保持一致。
- 注重现代极简 UI 审美，兼顾跨平台兼容性。
- 通用编码规范：见 `AGENTS.md §4`（命名）、§1（禁止越界）。

## 技术栈（模块特有）

- 语言：Python 3.14+
- 框架：PySide6 6.11.0（以 `requirements.txt` 为准）；**界面 = Web UI**：QtWebEngine 渲染前端工程 `ui/`（React + TS + Vite + Tailwind，与 Web 端同源快照、独立演进）
- 桥：`src/bridge/bridge.py`（白名单分发；原生窗口/播放器/文件对话框走**主线程方法**集合）
- 原生外壳：无边框窗口 + 系统托盘（`src/ui/tray.py`）+ 媒体原生浮层（`src/ui/media_overlay.py`）
- 界面状态管理：**在 `ui/src/stores/*`（zustand）**，不在 Python 侧做 UI 状态；HTTP：全部走 `src/utils/http_client.py`
- 加密：cryptography（与后端一致：ChaCha20-Poly1305 / ECDH X25519）
- 测试：pytest；风格检查：flake8
- 前端工程自测：`cd jflove-desktop/ui && npm run lint && npm run build`
- 构建：pyinstaller（`build.py` → `build/dist/JFLove`）；依赖：pip + `requirements.txt`
- 日志/异常/性能：与后端一致

## 核心组件（实际存在）

- `src/bridge/` — JS↔Python 桥，六个模块：
  - `bridge.py` — 白名单分发 + 响应回填（`MAIN_THREAD_METHODS` 三者求并集，见下）
  - `api_gateway.py` — 通用 `api.request` 透传（路径白名单 + 会话接口拒绝，见「渲染层硬约束」）
  - `files.py` / `media.py` / `sync.py` / `transfer.py` — 各域桥方法（`media.py` 另含 `dialogs.*` 原生对话框）
- `src/ui/web_shell.py` / `tray.py` / `media_overlay.py` — 原生外壳三件套
- `src/components/stream_proxy.py` — 本地解密 + Range 206 流代理（**媒体预览的唯一通路**）
- `src/utils/sync_engine.py` — 同步引擎（定时器 + 工作线程）
- `src/utils/transfer_manager.py` — 传输任务管理器
- `src/utils/worker.py` — QThread 异步工作线程

## 行为规范

- MVC 架构：`components/` / `services/` / `utils/` / `config/` / `ui/` / **`bridge/`**，各层归位。
- `services/` 封装所有后端调用，UI 层禁止直接发起 HTTP。
- 跨平台特定逻辑（路径、字体、托盘）必须做平台判断。
- 耗时操作用多线程，避免阻塞 UI 线程；全局异常 + 友好提示。
- 自测：`python -m flake8 src/ tests/ --max-line-length=99` + `python -m pytest tests/ -v`；
  **前端工程另有自己的门禁**：`cd jflove-desktop/ui && npm run lint && npm run build`（`ui/` 无 test 脚本，见 `testing` 技能）。
- 版本号**单一来源 = 仓库根 `version.json`**，完整规则见 `AGENTS.md §8.1`（清单以
  `scripts/sync_version.py::POSITIONS` 为准）。**桌面端只管 desktop scope 的 3 处**，
  `build.py` 构建前会校验，不一致直接中止构建（防"构建了新版本、应用内还是旧版本号"）：
  1. `src/config/settings.py` 的 `APP_VERSION`（Python 侧）
  2. **`ui/src/config/constants.ts` 的 `APP_VERSION`** —— 这是**用户可见**的那一处
     （`LoginPage.tsx` 登录页页脚 `v{APP_VERSION}`、`SettingsPage.tsx` 设置-关于 `JFLove v{APP_VERSION}`）
  3. `ui/package.json` 的 `version`（桌面端前端工程自身的包版本）
  **禁止手改**，UI 页禁止硬编码版本号。
- 前端产物：`ui/dist/` 由 `build.py` 以 `--add-data "ui/dist;webui"` 打进包内
  （onedir 下实际落在 `_internal/webui/`）。**`webui/` 只是包内资源目录名，源码树里不存在这个目录**，
  源码工程是 `jflove-desktop/ui/`。

> **版本迭代前置**：见 `AGENTS.md §7.6`。

## 渲染层硬约束（v1.5.0 换轨为 Web UI 后新增，不可降级）

1. **渲染层（JS）无网络出口**
   `ui/src` 里**不存在**任何 fetch / axios / XHR 直连后端的代码；一切与后端的通信只经
   `src/bridge/bridge.py` 的白名单桥。出网目标只由 Python 侧 `session_manager.server_url` 决定。

2. **`api.request` 的路径白名单 + 会话接口直接拒绝**
   `src/bridge/api_gateway.py`：
   - `ALLOWED_PATH_PREFIXES = ("/api/v1/",)`（`:31`）+ `ALLOWED_EXACT_PATHS = ("/health",)`（`:34`）——
     其余路径一律 `ValueError`；含 `://` 或 `..` 的路径直接拒绝（渲染层永远无法指定目标主机）。
   - `SESSION_MANAGED_PATHS`（`:56`，`/api/v1/auth/login` / `init-admin` / `/refresh`）**直接拒绝**通用透传
     （判定在 `:76`）—— 会话状态必须由 Python 服务层写入，否则会出现"HTTP 成功但 `session_manager.token`
     从未赋值 ⇒ 桥回答 `logged_in=False` ⇒ 页面永远跳不进业务面板"。
   - `TOKEN_BEARING_PATHS`（`:44`）返回前剥掉 JWT（纵深防御）。
   - 新增接口时**只加路径白名单**，不要为单个接口新写桥方法，也不要把会话类接口放行。

3. **文件字节与密钥严禁进入渲染层**
   上传 / 下载 / 预览全部在 Python 侧完成：媒体由 `StreamProxy`（`src/components/stream_proxy.py`）
   解密后交给 **OS 解码器**（`QMediaPlayer` + `QVideoWidget` / Qt 图像解码），
   渲染层只拿到**本地 URL 或本地文件路径**；session_key / JWT / 文件明文一律不出 Python 进程。

### `MAIN_THREAD_METHODS` 必须登记在**所属子模块**

原生窗口 / 播放器 / 对话框只能在 GUI 线程创建，它们的方法集合**分散在三个子模块**（不是集中在 `bridge.py`）：

| 子模块 | 定义行 | 内容 |
| --- | --- | --- |
| `src/bridge/media.py` | `:185` | `media.*` 全部 + `dialogs.{open_files,save_file,select_directory,message}`（`:187-190`） |
| `src/bridge/sync.py` | `:184` | `sync.{run,reload,status}` |
| `src/bridge/transfer.py` | `:151` | `transfer.{upload,download,cancel,clear}`（`:152-155`） |

`src/bridge/bridge.py:359-363` 对三者**求并集**后判定"是否在当前槽内联执行"。
**新增必须跑 GUI 线程的方法时，要在它所属的子模块登记**；漏登记不会报错，只会**静默跑到工作线程**，
表现为随机崩溃或"对话框不出现"。

## 跨端一致性门禁（硬约束，v1.5.1 新增）

桌面端与 Web 端「各自独立演进」，但**同源清单内的文件必须逐字节一致**。两个门禁都是纯标准库脚本，
**判据是退出码：0 = 通过，非 0 = 阻塞**。

```bash
python scripts/check_web_desktop_parity.py            # 同源快照：PARITY_MANIFEST 内文件两端 sha256 必须相同
python scripts/check_web_desktop_parity.py --self-test # 门禁自身的反向验证（篡改/缺文件/清单腐烂必须被拦住）
python scripts/check_logo_sync.py                     # 品牌资产：两端 public/logo-* 同字节 + 源码引用反查
python scripts/check_logo_sync.py --self-test          # 同上，反向验证
```

- **`PARITY_MANIFEST` 当前含三个文件**（相对各自前端工程根，即 `jflove-web/src/...` 与 `jflove-desktop/ui/src/...`）：
  1. `src/pages/NoteEditPage.tsx`
  2. `src/styles/components.css`
  3. `src/styles/tokens.css`（设计令牌的唯一真相）
- **清单内文件在 `jflove-web/` 与 `jflove-desktop/ui/` 下必须逐字节一致 —— 改一端必须两端同改**，
  否则 CI job **`cross-end-consistency-guard`**（`.github/workflows/ci-guards.yml`）变红。
  清单会随版本增补，**以 `scripts/check_web_desktop_parity.py::PARITY_MANIFEST` 为准**。
- 确需两端分叉时，必须**从 `PARITY_MANIFEST` 中移除该文件并写明原因**，不能只改一端。
- 本地提交前先跑上面 4 条命令；门禁报"清单腐烂"说明清单里点了不存在的文件，同样要修。

## 文档更新范围

- 路径：`文档记录/桌面端开发记录/<版本号>.md`；同步更新 `jflove-desktop/README.md`
- 必须包含：功能与改动点、页面/组件/服务方法、调用的后端接口、逻辑对比、设计取舍

## 开发环境

详见 `AGENTS.md §3.1` 的 Python venv 路径约定与自测命令。

## 安全宪法

详见 `AGENTS.md §9`。你的角色约束见 §9.6 表格 `cross-platform-desktop` 行。
