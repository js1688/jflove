# JFLove Desktop

JFLove 桌面客户端，基于 PySide6 + PySide6-Fluent-Widgets 构建的私有文档与笔记管理系统。

## 功能特性

- **文档管理**：虚拟磁盘浏览、文件/目录上传下载、图片与文本预览、视频/音频/文本流式预览（v1.1.0）
- **笔记管理**：Markdown 编辑器（实时预览）、新建/重命名/删除笔记
- **安全通信**：X25519 ECDH 密钥交换 + ChaCha20-Poly1305 端到端加密
- **管理员功能**：用户管理（增删/改密/启禁）、虚拟磁盘管理、权限配置
- **服务端地址历史**（v1.0.1）：登录页与设置页的服务端地址输入框均为可编辑下拉，自动记录连接成功的地址（最近 10 条），下次启动默认显示上次连接位置；历史保存在用户数据目录（`%APPDATA%/JFLove/storage/server_history.json`），仅存 URL 字符串，不含任何 token / 密码
- **流式预览**（v1.1.0）：视频/音频通过 StreamProxy 本地 HTTP 代理零缓存播放；文本通过 StreamTextLoader 逐帧流式加载，首屏 ≤3s；不再写本地临时文件
- **会话稳定性**（v1.1.1）：登录界面新增「登录有效期」下拉（30分/1小时/2小时/4小时/8小时，默认 1 小时，记忆上次选择）；服务端重启 / ECDH 加密会话失效场景由 HTTP 客户端自动重新交换密钥并透明重发请求，用户无感知；JWT 真到期则只弹一次"登录已过期"提示并切换到唯一登录窗
- **登录有效期真生效**（v1.1.2）：登录请求把用户选择的 TTL 上传给服务端，服务端按此签发 JWT（受服务端硬上限 8 小时保护）；登录页布局优化：「登录有效期」标签 + 下拉同行、「返回」+「登录」按钮同行
- **文档管理扩展**（v1.1.3）：右键菜单新增「重命名」与「移动到…」操作；「移动到…」弹出懒加载目录树选择器（`MoveTargetDialog`）；无写权限时三项写操作（重命名/移动/删除）均禁用；磁盘列表接口新增 `can_write` 字段，切换磁盘时实时更新权限状态

## 环境要求

- Python 3.14+
- 依赖包见 `requirements.txt`

## 安装依赖

> **多平台共存**：项目源码在 Linux / Windows / macOS 之间通过同步盘共享时，**必须为每个平台单独建一个 venv**（虚拟环境内含 absolute path、平台二进制，跨平台无法复用）。本项目约定按平台后缀命名：
>
> | 平台 | 目录 |
> |---|---|
> | Linux | `venv-linux/` |
> | Windows | `venv-win/` |
> | macOS | `venv-mac/` |
>
> 三个目录内部都自带 `.gitignore: *`（Python `venv` 模块默认生成），不会被 git 跟踪；`build.py` 不绑定 venv 路径，谁的 python 跑它就用谁的依赖。

### 创建虚拟环境

**Linux:**
```bash
cd jflove-desktop
python3 -m venv venv-linux
source venv-linux/bin/activate
pip install -r requirements.txt
```

**Windows (PowerShell):**
```powershell
cd jflove-desktop
python -m venv venv-win
.\venv-win\Scripts\Activate.ps1
pip install -r requirements.txt
```

**Windows (CMD):**
```cmd
cd jflove-desktop
python -m venv venv-win
venv-win\Scripts\activate.bat
pip install -r requirements.txt
```

**macOS:**
```bash
cd jflove-desktop
python3 -m venv venv-mac
source venv-mac/bin/activate
pip install -r requirements.txt
```

> **同步盘提醒**：如果你用云盘 / Samba 在 Windows 与 Linux 之间同步代码，请把 `venv-*/` 目录加入同步软件的**排除列表**（OneDrive / Dropbox 等都支持），否则跨平台同步过来的 venv 会因为 absolute path 与平台二进制不兼容而失效。

## 运行

确保 `jflove-server` 已启动并可访问，然后：

```bash
python src/main.py
```

首次运行时，应用会引导用户：
1. 输入服务端地址（默认 `http://localhost:8989`）
2. 若系统尚无管理员，创建管理员账号
3. 登录进入主界面

## 构建可执行文件

```bash
pyinstaller --onefile --windowed src/main.py --name JFLove
```

构建产物在 `build/` 目录下。

## 项目结构

```
src/
├── main.py                   # 应用入口
├── config/settings.py        # 全局常量（服务端地址、加密盐值等）
├── components/
│   ├── upload_progress.py    # 分片上传进度对话框
│   ├── preview_dialog.py     # 通用文件预览对话框（图片/SVG/视频/音频/文本/Markdown）
│   ├── stream_proxy.py       # 本地流式 HTTP 代理（视频/音频专用，v1.1.0）
│   └── stream_text_loader.py # 文本流式加载线程（v1.1.0）
├── services/                 # 与后端 API 交互的业务服务层
│   ├── auth_service.py       # 认证（密钥交换/登录/登出）
│   ├── file_service.py       # 文件管理（分片上传/下载/预览）
│   ├── note_service.py       # 笔记管理
│   ├── user_service.py       # 用户管理（管理员）
│   ├── disk_service.py       # 虚拟磁盘管理（管理员）
│   ├── permission_service.py # 权限配置（管理员）
│   └── config_service.py     # 服务端配置查询
├── ui/
│   ├── login_window.py       # 登录/初始化窗口
│   ├── main_window.py        # 主窗口（FluentWindow + 侧边导航）
│   └── pages/
│       ├── file_page.py      # 文档管理
│       ├── note_page.py      # 笔记管理
│       ├── security_page.py  # 安全状态
│       ├── settings_page.py  # 设置
│       └── admin/
│           ├── user_page.py       # 用户管理
│           ├── disk_page.py       # 磁盘管理
│           └── permission_page.py # 权限配置
└── utils/
    ├── crypto.py       # X25519 + HKDF + ChaCha20-Poly1305
    ├── http_client.py  # 加密 HTTP 客户端
    ├── session.py      # 会话状态单例
    ├── worker.py       # QThread 异步工作线程
    └── logger.py       # 日志工具
```

## 加密说明

所有 API 请求（除密钥交换和管理员检测外）均采用端到端加密：

| 环节     | 算法                               |
| -------- | ---------------------------------- |
| 密钥交换 | X25519 ECDH + HKDF-SHA256          |
| 数据加密 | ChaCha20-Poly1305（12 字节 nonce） |
| 身份认证 | JWT（ES256，通过加密 Body 传递）   |

会话密钥仅保存在内存中，程序重启后需重新进行密钥交换。

## 日志与持久化存储

v1.1.5 起，所有持久化数据（日志、会话信息、服务端地址历史）统一保存在用户数据目录：

| 平台 | 路径 |
|------|------|
| Windows | `%APPDATA%/JFLove/` → `C:\Users\<用户名>\AppData\Roaming\JFLove\` |
| Linux | `~/.local/share/JFLove/` |
| macOS | `~/Library/Application Support/JFLove/` |

目录结构：
```
JFLove/
├── storage/
│   ├── session.json            # 会话信息（token、server_url、username 等）
│   └── server_history.json     # 服务端地址历史（最近 10 条）
└── logs/
    ├── app.log                 # INFO 及以上级别日志
    └── error.log               # ERROR 级别日志
```

## 开发记录

| 版本 | 文档 |
| --- | --- |
| v1.0 | `文档记录/桌面端开发记录/v1.0.md` |
| v1.0.1 | `文档记录/桌面端开发记录/v1.0.1.md` |
| v1.1.0 | `文档记录/桌面端开发记录/v1.1.0.md` |
| v1.1.1 | `文档记录/桌面端开发记录/v1.1.1.md` |
| v1.1.2 | `文档记录/桌面端开发记录/v1.1.2.md` |
| v1.1.3 | `文档记录/桌面端开发记录/v1.1.3.md` |
| v1.1.4 | `文档记录/桌面端开发记录/v1.1.4.md` |
| v1.1.5 | `文档记录/桌面端开发记录/v1.1.5.md` |
