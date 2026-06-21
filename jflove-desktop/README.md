# jflove-desktop

JFLove 桌面客户端，基于 PySide6 + PySide6-Fluent-Widgets 构建的私有文档与笔记协同管理桌面应用。

---

## 技术架构

### 框架与组件

| 组件 | 用途 |
|------|------|
| **PySide6 6.8.0.2** | Qt 绑定，UI 框架 |
| **PySide6-Fluent-Widgets** | Material Design 风格组件库 |
| **QWebEngineView** | Markdown 预览渲染（GitHub 风格 CSS + highlight.js + Mermaid） |

### 架构模式

采用 **Redux 模式**（基于信号槽实现）：

```
UI (Page) → Signal → Service (业务逻辑) → http_client → 后端 API
    ↑                      ↓
    └── Signal ←─── Response ────┘
```

| 层 | 目录 | 职责 |
|----|------|------|
| **UI 页面** | [`src/ui/pages/`](jflove-desktop/src/ui/pages/) | 用户界面、交互事件 |
| **UI 窗口** | [`src/ui/`](jflove-desktop/src/ui/) | 登录窗口、主窗口（FluentWindow + 侧边导航） |
| **组件** | [`src/components/`](jflove-desktop/src/components/) | 可复用 UI 组件（预览对话框、流式代理、文本加载器） |
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
- **UI 框架**：PySide6 6.8.0.2
- **组件库**：PySide6-Fluent-Widgets
- **HTTP**：requests
- **加密**：cryptography（X25519 / HKDF-SHA256 / ChaCha20-Poly1305）
- **构建**：PyInstaller（`--onefile --windowed`）

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

## 构建可执行文件

```bash
cd jflove-desktop
python build.py             # 默认 onefile + windowed
python build.py --clean     # 构建前先 rm -rf build/
```

### 构建产物

| 平台 | 产物路径 | 说明 |
|------|---------|------|
| Linux | `build/dist/JFLove` | ELF 单文件 |
| Windows | `build/dist/JFLove.exe` | 带 icon.ico（需 Windows 主机构建） |
| macOS | `build/dist/JFLove.app` | 建议手动 codesign（需 macOS 主机构建） |

---

## 项目结构

```
jflove-desktop/
├── src/
│   ├── main.py                   # 应用入口
│   ├── config/settings.py        # 全局配置（版本号、上传策略、加密盐值等）
│   ├── components/
│   │   ├── preview_dialog.py     # 通用文件预览对话框
│   │   ├── stream_proxy.py       # 本地流式 HTTP 代理（视频/音频）
│   │   └── stream_text_loader.py # 文本流式加载线程
│   ├── services/
│   │   ├── auth_service.py       # 认证（密钥交换/登录/登出/会话持久化）
│   │   ├── file_service.py       # 文件管理（分片上传/下载/预览）
│   │   ├── note_service.py       # 笔记管理
│   │   ├── user_service.py       # 用户管理（管理员）
│   │   ├── disk_service.py       # 虚拟磁盘管理（管理员）
│   │   ├── permission_service.py # 权限配置（管理员）
│   │   ├── config_service.py     # 服务端配置查询
│   │   ├── sync_service.py       # 同步配置管理（本地 JSON 文件）
│   │   └── server_history_service.py  # 服务端地址历史
│   ├── ui/
│   │   ├── login_window.py       # 登录/初始化窗口
│   │   ├── main_window.py        # 主窗口（FluentWindow + 侧边导航）
│   │   └── pages/
│   │       ├── file_page.py      # 文档管理
│   │       ├── note_page.py      # 笔记管理
│   │       ├── security_page.py  # 安全状态
│   │       ├── settings_page.py  # 设置
│   │       ├── sync_page.py      # 同步目录
│   │       ├── transfer_page.py  # 传输任务
│   │       └── admin/
│   │           ├── user_page.py       # 用户管理
│   │           ├── disk_page.py       # 磁盘管理
│   │           └── permission_page.py # 权限配置
│   └── utils/
│       ├── crypto.py       # X25519 + HKDF + ChaCha20-Poly1305
│       ├── http_client.py  # 加密 HTTP 客户端
│       ├── session.py      # 会话状态单例
│       ├── sync_engine.py  # 同步引擎（定时器 + 工作线程）
│       ├── transfer_manager.py  # 传输任务管理器
│       ├── worker.py       # QThread 异步工作线程
│       ├── icon.py         # 图标工具
│       ├── input_method.py # 输入法兼容
│       └── logger.py       # 日志工具
├── tests/                  # 测试代码
├── images/                 # 应用图标
├── build.py                # PyInstaller 构建脚本
├── requirements.txt        # 依赖清单
└── README.md               # 本文件
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

核心依赖（详见 [`requirements.txt`](jflove-desktop/requirements.txt)）：

| 依赖 | 用途 |
|------|------|
| `PySide6==6.8.0.2` | Qt 绑定，UI 框架 |
| `PySide6-Fluent-Widgets` | Material Design 风格组件库 |
| `cryptography` | X25519 / ChaCha20-Poly1305 加密 |
| `requests` | HTTP 客户端 |
| `PyJWT` | JWT 验证（ES256） |
| `bcrypt` | 密码哈希 |
| `pytest` | 测试框架 |
| `flake8` | 代码风格检查 |
| `pyinstaller` | 应用打包 |
| `markdown` | Markdown→HTML 渲染 |
| `pygments` | 代码语法高亮 |
| `pdfminer.six` | PDF 文本提取 |
| `python-docx` | DOCX 文本提取 |
