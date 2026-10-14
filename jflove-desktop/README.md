# jflove-desktop

JFLove 桌面客户端：**原生外壳（PySide6）+ Web UI（QtWebEngine 渲染）** 的私有文档与笔记协同管理桌面应用，界面与 Web 端同一套，同时保留托盘、本地解码的媒体预览、原生文件对话框与系统拖放上传。

> **当前版本：v1.5.1**（版本号**单一来源 = 仓库根 `version.json`**；`python scripts/sync_version.py` 同步全部位置 —— 桌面端有**三处**（**以 `scripts/sync_version.py::POSITIONS` 的 desktop scope 为准**）：`src/config/settings.py` 的 `APP_VERSION`、**`ui/src/config/constants.ts`** 的 `APP_VERSION`（界面上「登录页页脚 / 设置-关于」显示的值）与 **`ui/package.json`** 的 `version`。**不要手改任何一处版本号字段。**）

> **v1.5.1**：Web 端品牌标识与桌面端统一（猫猫 logo）；系统设置移除**已失效**的「允许重编码降级」开关（服务端自 v1.4.2 起已不读取该键）；
> 修复原生媒体浮层「关窗后残留」与「拖动主窗画面不跟随」；笔记页大纲改为**右侧可折叠竖栏**、打开笔记默认进入预览模式（有内容时）；
> 修复「200 响应是加密信封但解不开却被当明文成功」的静默降级；版本号同步补入界面侧常量（此前界面会显示旧版本号）。
>
> **v1.5.0**：界面由 Qt Widgets **整体迁移到 Web UI**（QtWebEngine 渲染 `ui/`），保留托盘、本地解码媒体预览、原生文件对话框与系统拖放上传；交付形态改为**安装包**（onedir + Inno Setup / RPM）。

---

## v1.6.0：内置文本编辑器（Scratch Editor，**纯本地**）

应用内**浮动编辑窗格**：可同时开多个、互不干扰，切换路由不丢失（挂在窗口标题栏之下、路由之上）。
**与远端零交互**（不读/不写虚拟磁盘与笔记）—— 内容只落本机草稿库或用户指定的本地文件。

| 能力 | 说明 |
|------|------|
| 内核 | **CodeMirror 6**，以**单文件 IIFE 离线分包**在运行期从 `ui/public/vendor/editor.bundle.js` 懒加载（首次呼出窗格才加载；`npm` 侧**不**引 `@codemirror/*` 依赖） |
| 块选择 | 修饰键（默认 `Alt`）+ 拖拽 = **矩形块选择**；复制口径为 IDE 行为（逐行取列区间、短行右侧补空格到块宽、`\n` 连接） |
| 差异化体验 | 按类型提供 格式化 / 压缩 / JSON 树 / Markdown 预览与分屏 / CSV 表格视图 / 行级 diff / 折叠 / 括号匹配；SQL 美化可配（方言 / 关键字大小写 / 缩进） |
| 工具箱 | 12 项：查找替换（含正则）+ 跳转行、行操作（排序/去重/去空行/去首尾空格）、大小写转换、JSON·HTML·URL 转义、Base64、JSON 树 + 格式化/压缩、Markdown 预览/分屏、CSV 表格、SQL 美化、**XML 格式化/压缩（自研，保注释与 CDATA）**、两段文本对比、字号与行号/换行开关 |
| 类型校验先行 | 扩展名白名单 → `stat` 大小阈值（**> 50 MB 在读内容前拒绝**；> 5 MB 降级只读但内容完整）→ 一次有界读 64 KB → 前 8 KB 嗅探（BOM / NUL / **严格解码优先**）→ 编码判定（utf-8 / utf-8-bom / gbk / utf-16le / utf-16be） |
| 保存语义 | 新建首次保存**必须**选路径；之后 `Ctrl+S` 直接更新该文件；**自动保存 1500 ms** 仅对**有保存路径**的文档生效；从未保存过的文档**永不**被静默写入磁盘（只进草稿库） |
| 草稿与还原 | 草稿库 `USER_DATA_DIR/storage/scratch/`（`index.json` + `pane-<id>.txt`，各自原子写）；**崩溃/被杀后下次启动自动还原全部窗格**（懒挂载，几十份也不拖慢启动）；正常退出弹「保存 / 放弃 / 取消 + 是否保留下次恢复」 |
| 快捷键 | 20 项默认键位、全部可配置（设置页「文本编辑器」+ 配置对话框，含**中文功能说明**、冲突检测、一键恢复默认）；**不注册任何全局热键**（只在窗口激活时生效） |
| 文件关联 | 安装包注册到系统「打开方式」候选（Windows 写 **HKCU**；Linux 装 `application/x-jflove-text` MIME）；**不抢占默认程序**；应用内勾选只声明 JFLove 自己支持的范围（勾选状态落 `USER_DATA_DIR/storage/file_assoc.json`） |
| 单实例 | 第二实例**不新开窗口/托盘**，把文件路径转交给已运行实例后退出（`QLocalServer` 行分隔 JSON 协议 + `ack` 确认；转交失败降级为本地启动，绝不静默丢弃） |
| 安全边界 | 渲染层**无网络出口**、编辑器的远端调用 **0 个**；文件字节不进渲染层；日志不记文档内容与完整路径（`AGENTS.md §9`） |

> 单一真相：类型/扩展名表只写在 [`ui/src/config/scratch-types.ts`](jflove-desktop/ui/src/config/scratch-types.ts)，
> `ui/public/file-assoc.manifest.json` 与三处打包资产（`packaging/linux/jflove.xml`、
> `.desktop` 的 `MimeType=` 行、`packaging/windows/file-assoc.iss`）**全部由脚本派生**，
> 一致性判据：`python jflove-desktop/scripts/gen_file_assoc_assets.py --check`（退出码 0 = 一致）。

> 构建注记：CM6 离线分包**不入库**，由 `build.py::ensure_editor_bundle()` 在缺失时**就地生成**
> （`scripts/editor-bundle` 装依赖 + 跑 `scripts/build-codemirror-bundle.mjs`），生成失败才响亮失败
> —— 这样无需改 CI 也能保证打包产物不缺内核。

---

## 技术架构

### 框架与组件

| 组件 | 用途 |
|------|------|
| **PySide6 6.11.0** | Qt 绑定；原生外壳（窗口 / 托盘 / 媒体浮层）与 QtWebEngine 宿主 |
| **QtWebEngine** | 渲染前端工程 `ui/`（React + TS + Vite + Tailwind） |
| **Markdown 预览** | 渲染层 React 组件 [`ui/src/components/markdown/MarkdownRenderer.tsx`](jflove-desktop/ui/src/components/markdown/MarkdownRenderer.tsx)（marked + DOMPurify + highlight.js + Mermaid）；Qt 侧旧的 `src/components/markdown_view.py` 已随 Qt Widgets 界面删除 |

### 架构模式

采用 **Redux 模式**（基于信号槽实现）：

```
UI (Page) → Signal → Service (业务逻辑) → http_client → 后端 API
    ↑                      ↓
    └── Signal ←─── Response ────┘
```

| 层 | 目录 | 职责 |
|----|------|------|
| **UI 前端工程** | [`ui/`](jflove-desktop/ui/) | 全部业务界面（React + TS + Vite + Tailwind），与 Web 端同源快照、独立演进 |
| **原生外壳** | [`src/ui/`](jflove-desktop/src/ui/) | `web_shell.py`（无边框窗口 / 桥注入 / 关闭=最小化到托盘）、`tray.py`、`media_overlay.py`（原生媒体浮层） |
| **渲染层桥** | [`src/bridge/`](jflove-desktop/src/bridge/) | 白名单桥（`bridge.py` + `api_gateway.py` / `files.py` / `media.py` / `sync.py` / `transfer.py`）；JS **无网络出口** |
| **组件（原生能力）** | [`src/components/`](jflove-desktop/src/components/) | `stream_proxy.py`（本地解密 + Range 206 流代理，媒体预览的唯一通路） |
| **服务** | [`src/services/`](jflove-desktop/src/services/) | 与后端 API 交互的业务服务层 |
| **工具** | [`src/utils/`](jflove-desktop/src/utils/) | 加密、HTTP 客户端、会话管理、工作线程 |

### 通信层

```
Service Layer → http_client.py → 加密请求 → 后端 API
                    ↑
              crypto.py (X25519 + ChaCha20-Poly1305)
```

- 所有 HTTP 调用必须通过 [`src/utils/http_client.py`](jflove-desktop/src/utils/http_client.py)，禁止在 services/ui 层直接 `import requests`
- 自动处理：密钥交换 → 会话维护 → 请求加密 → 响应解密 → 错误响应解密（`_decrypt_envelope_or_none`）
- 服务端重启后自动重新交换密钥并透明重发请求（用户无感知）

### 运行环境

- **语言**：Python 3.14+
- **UI 框架**：PySide6 6.11.0
- **界面**：Web UI（`ui/`，与 Web 端同源快照、独立演进）
- **HTTP**：requests
- **加密**：cryptography（X25519 / HKDF-SHA256 / ChaCha20-Poly1305）
- **构建**：PyInstaller（**onedir**；交付形态为**安装包**，见「构建与打包」）

> **Python 版本口径（BACKLOG BL-5 留痕，2026-10-14 实测）**
>
> - **要求侧**：Python **3.14+** —— 本文件上方「语言」、`AGENTS.md §2`、根 `README.md` badge、
>   `jflove-server/Dockerfile:5`（`FROM python:3.14-slim`）、CI workflow 均为 3.14。
> - **本地实测侧**：`venv-win/` 实际为 **3.13.14**（本机 `Programs\Python` 下**只有 `Python313`**，
>   无 3.14、无 `py` launcher）。**v1.6.0 的全部用例在该版本下全绿**：
>   `flake8 src/ tests/ --max-line-length=99` → 0 警告；`pytest tests/` → **133 passed**（v1.5.1 基线）。
> - **本次决定（D14）：不升级本地环境**。差异的性质是「**本地环境低于项目要求**」，
>   **不是文档写错**；CI 与镜像继续使用 3.14。
> - 「把本地 venv 升到 3.14」已留在 `文档记录/项目管理记录/BACKLOG.md`，不在 v1.6.0 内做。

---

## 环境要求与依赖安装

> **多平台共存**：项目源码在 Linux / Windows / macOS 之间通过同步盘共享时，**必须为每个平台单独建一个 venv**（虚拟环境内含 absolute path、平台二进制，跨平台无法复用）。本项目约定按平台后缀命名：

| 平台 | 虚拟环境目录 |
|------|------------|
| Linux | `venv-linux/` |
| Windows | `venv-win/` |
| macOS | `venv-mac/` |

### Linux

```bash
cd jflove-desktop
python3 -m venv venv-linux
source venv-linux/bin/activate
pip install -r requirements.txt
```

### Windows (PowerShell)

```powershell
cd jflove-desktop
python -m venv venv-win
.\venv-win\Scripts\Activate.ps1
pip install -r requirements.txt
```

### macOS

```bash
cd jflove-desktop
python3 -m venv venv-mac
source venv-mac/bin/activate
pip install -r requirements.txt
```

> **同步盘提醒**：使用云盘 / Samba 同步代码时，请把 `venv-*/` 目录加入同步软件的排除列表，否则跨平台同步来的 venv 会因 absolute path 与平台二进制不兼容而失效。

---

## 运行

确保 `jflove-server` 已启动并可访问：

```bash
cd jflove-desktop
python src/main.py
```

### 首次引导流程

1. 输入服务端地址（默认 `http://localhost:8989`）
2. 若系统尚无管理员，创建管理员账号
3. 登录进入主界面

---

## 构建与打包

> **统一入口在仓库根**（`AGENTS.md §8`）：本地打包一律走根目录 `python build.py -m desktop`（自动同步版本号 + 逐模块环境检查 + 切换到本模块 venv）。
> 直接调用本模块 `build.py` 只用于单步调试，**不作为交付路径**。

```bash
# 仓库根目录执行（唯一对外入口）
python build.py -m desktop      # Windows：onedir + 安装包 + 便携 zip；Linux：onedir + RPM
python build.py -m all          # 四端统一入口（缺 docker 时 server/web 会响亮跳过）
```

### 构建产物（桌面端交付形态 = **安装包**）

| 平台 | 产物 | 说明 |
|------|------|------|
| Windows | `build/dist/JFLove-<ver>-win64-setup.exe`、`JFLove-<ver>-win64-portable.zip`、`build/dist/JFLove/` | Inno Setup 安装包（含开始菜单/卸载器）；缺 Inno Setup 时**降级只出 zip 并响亮报警**（需 Windows 主机构建） |
| Linux / Fedora | `jflove-desktop-<ver>-1.fc<N>.x86_64.rpm`、`build/dist/JFLove/` | 由 `packaging/linux/build_rpm.sh` / CI 产出 |
| 其他 | `build/dist/JFLove/` | 只出 onedir（PyInstaller 不能交叉编译） |

> `--mode onefile`（单体 exe）仅为**兼容保留的显式选项**，**禁止**作为默认或交付形态：onefile 每次启动要解压数百 MB（实测「启动→首窗可见」中位 9.58s → onedir 1.31s，快约 7.3×），且 onedir 才配得上正常安装模式。

---

## 项目结构

```
jflove-desktop/
├── src/
│   ├── main.py                     # 应用入口（构造 WebShell + 原生浮层 + 桥装配）
│   ├── bridge/                     # JS↔Python 白名单桥（渲染层无网络出口）
│   │   ├── bridge.py               # 分发入口 + 向渲染层推事件
│   │   ├── api_gateway.py          # api.request 路径白名单（拒绝会话管理类路径）
│   │   ├── files.py                # 原生文件对话框 / 拖放 / 下载保存
│   │   ├── media.py                # 媒体打开/几何上报/播放控制的桥接
│   │   ├── sync.py                 # 同步引擎信号 → 桥事件
│   │   └── transfer.py             # 传输任务信号 → 桥事件
│   ├── components/
│   │   └── stream_proxy.py         # 本地解密 + Range 206 流代理（媒体预览唯一通路）
│   ├── config/settings.py          # 全局配置（APP_VERSION 由 version.json 同步）
│   ├── services/                   # 业务服务层（唯一 HTTP 出口在 utils/http_client.py）
│   │   ├── auth_service.py / file_service.py / note_service.py / user_service.py
│   │   ├── disk_service.py / permission_service.py / repair_service.py
│   │   └── config_service.py / sync_service.py / server_history_service.py
│   ├── ui/                         # 原生外壳（无边框窗口 / 托盘 / 媒体浮层）
│   │   ├── web_shell.py            # 承载 QtWebEngine、注入桥、关闭=最小化到托盘
│   │   ├── tray.py                 # 系统托盘（显示窗口 / 主题 / 退出）
│   │   └── media_overlay.py        # 原生媒体浮层（贴 Web 预留矩形，OS 解码器渲染）
│   └── utils/
│       ├── crypto.py               # X25519 + HKDF + ChaCha20-Poly1305
│       ├── http_client.py          # 加密 HTTP 客户端（含信封形状判定）
│       ├── session.py              # 会话状态单例
│       ├── sync_engine.py          # 同步引擎（定时器 + 工作线程）
│       ├── transfer_manager.py     # 传输任务管理器
│       ├── worker.py               # QThread 异步工作线程
│       └── icon.py / input_method.py / logger.py
├── ui/                             # 前端工程（React + TS + Vite + Tailwind，与 Web 端同源快照）
│   ├── src/                        # pages / components / layouts / stores / services / styles / hooks / types
│   ├── public/                     # logo-64/128/256.png 等静态资源
│   └── dist/                       # 前端产物（构建生成；打包时作为 webui/ 进包）
├── packaging/                      # 打包资产（Windows Inno Setup / Linux RPM / spec / 桌面项）
├── tests/                          # pytest（含 v1.5.1 浮层生命周期与信封用例）
├── poc/                            # 一次性验证脚本（非交付物）
├── images/                         # 应用图标 icon.ico / icon.png
├── build.py                        # 模块构建脚本（onedir + 安装包；交付走仓库根 build.py）
├── requirements.txt                # 运行时 + 打包依赖（pytest / flake8 为开发依赖，不入此文件）
└── README.md                       # 本文件
```

---

## 加密协议

所有 API 请求（除明文白名单外）均采用端到端加密：

| 环节 | 算法 |
|------|------|
| 密钥交换 | X25519 ECDH + HKDF-SHA256（盐 `b"jflove-v1"`，32B 派生密钥） |
| 数据加密 | ChaCha20-Poly1305（12 字节随机 nonce） |
| 身份认证 | JWT（ES256，通过加密 Body 传递） |
| 文件流加密 | 64 KB 分片独立加密帧 `[4B 长度][12B nonce][密文+16B tag]` |

会话密钥仅保存在内存中，程序重启后需重新进行密钥交换。

---

## 用户数据目录

v1.1.5+，所有持久化数据统一保存在用户数据目录：

| 平台 | 路径 |
|------|------|
| Windows | `%APPDATA%/JFLove/` → `C:\Users\<用户名>\AppData\Roaming\JFLove\` |
| Linux | `~/.local/share/JFLove/`（或 `$XDG_DATA_HOME/JFLove`） |
| macOS | `~/Library/Application Support/JFLove/` |

### 目录结构

```
JFLove/
├── storage/
│   ├── session.json            # 会话信息（token、server_url、username 等）
│   ├── server_history.json     # 服务端地址历史（最近 10 条）
│   └── sync_configs.json       # 同步配置（v1.1.6+，本地化管理）
└── logs/
    ├── app.log                 # INFO 及以上级别日志
    └── error.log               # ERROR 级别日志
```

> **安全说明**：`sync_configs.json` 仅存配置元数据（不存 token / session_key / 密码）。`session.json` 不存储 session_key 或密码。

---

## 依赖清单

核心依赖（详见 [`requirements.txt`](jflove-desktop/requirements.txt)，仅运行时 + 打包依赖；`pytest`/`flake8` 为开发依赖，不入 requirements.txt）：

| 依赖 | 用途 |
|------|------|
| `PySide6==6.11.0`（含 QtWebEngine / QtMultimedia） | Qt 绑定；原生外壳 + 界面渲染引擎 + 本地解码播放 |
| `cryptography==47.0.0` | X25519 / HKDF-SHA256 / ChaCha20-Poly1305 加密 |
| `requests==2.33.1` | HTTP 客户端（仅 `src/utils/http_client.py` 使用） |
| `pyinstaller==6.20.0` | 应用打包 |

> **更正（v1.5.1）**：本表此前还列了 `markdown` 与 `pygments` —— 那是 Qt Widgets 版 Markdown 预览的遗留依赖，
> 界面迁移到 Web UI 后已从 `requirements.txt` 移除。实测：`requirements.txt` 只有上表 4 项，且 `src/` 下 0 处 `import markdown / pygments`。
