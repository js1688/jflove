# JFLove — 私有文档与笔记协同管理系统

![Python](https://img.shields.io/badge/Python-3.14%2B-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green) ![PySide6](https://img.shields.io/badge/PySide6-6.8.0.2-orange) ![Flutter](https://img.shields.io/badge/Flutter-3.27%2B-blue) ![Dart](https://img.shields.io/badge/Dart-3.6%2B-blue) ![React](https://img.shields.io/badge/React-18-blue) ![TypeScript](https://img.shields.io/badge/TypeScript-5.7-blue) ![Vite](https://img.shields.io/badge/Vite-6-purple) ![License](https://img.shields.io/badge/License-Proprietary-red)

JFLove 是一款面向个人用户的私有化文档与笔记协同管理系统，提供服务端 + 桌面客户端 + 移动端 + Web 端四端架构。所有通信经过端到端加密（X25519 ECDH + ChaCha20-Poly1305），保障用户数据隐私。

---

## 开发环境

> 本项目为 **全 AI 自动化开发** 项目，从需求分析、架构设计、代码实现到测试发布，全部由 AI 模型在规范化流程驱动下完成。

### 开发工具链

| 工具 | 说明 |
|------|------|
| **操作系统** | Linux 7.0（开发主力）/ Windows / macOS（桌面端/iOS 构建） |
| **IDE** | Visual Studio Code |
| **AI 插件** | GitHub Copilot + DeepSeek V4 for Copilot Chat（集成 deepseek-v4-pro / deepseek-v4-flash 双模型，驱动多角色 AI 代理工作流） |
| **AI 模型** | deepseek-v4-pro（复杂任务主用）+ deepseek-v4-flash（轻量/高频任务）（均通过 GitHub Copilot Chat 接入） |
| **Node.js** | 22+ / npm 10+（Web 端构建与运行，Vite 6） |
| **代码风格** | EditorConfig 统一缩进（2 空格，UTF-8，LF 换行） |
| **调试配置** | VS Code launch.json 预设服务端/桌面端/Web 端（Edge）启动配置 |

### AI 代理角色

项目采用 **多代理协作架构**，通过 `.claude/skills/` 目录下的技能文件驱动 10 个 AI 角色协同工作：

| 角色 | 职责 |
|------|------|
| **Product** | 需求挖掘、编写需求文档 |
| **Designer** | 系统架构、数据库、API 设计 |
| **Backend** | 后端业务代码实现（FastAPI） |
| **Desktop** | 桌面端 UI 与交互实现（PySide6） |
| **Mobile** | 移动端 UI 与交互实现（Flutter + Dart） |
| **Web** | Web 端 UI 与交互实现（React + TypeScript） |
| **Code Review** | 代码规范、质量、安全审查 |
| **Testing** | 自动化测试用例编写与执行 |
| **PMO** | 项目管理、任务跟踪、版本控制 |
| **DevOps** | 构建、打包、部署、发布 |

> 详细角色规范与工作流程见 [`AGENTS.md`](AGENTS.md)。

---

## 模块概览

| 模块 | 路径 | 说明 | 当前状态 |
|------|------|------|---------|
| **jflove-server** | [`jflove-server/`](jflove-server/) | 后端服务（FastAPI + SQLite） | ✅ 在研 |
| **jflove-desktop** | [`jflove-desktop/`](jflove-desktop/) | 跨平台桌面客户端（PySide6） | ✅ 在研 |
| **jflove-app** | [`jflove-app/`](jflove-app/) | 跨平台移动端 App（Flutter + Dart），首版 Android-only，iOS/鸿蒙预留 | ✅ 在研 |
| **jflove-web** | [`jflove-web/`](jflove-web/) | 浏览器 Web 端（React + TypeScript），支持 PC 端与移动端浏览器双布局 | ✅ 在研 |

> **模块边界**：各模块独立开发，互不越界。详细代码规范与工作手册见 [`AGENTS.md`](AGENTS.md)。

---

## 功能特性

### 📁 文档管理

- **虚拟磁盘**：服务端磁盘目录映射为虚拟磁盘，完全依赖文件系统结构，不在数据库存储文件目录信息
- **目录浏览**：树形结构展示服务端磁盘目录，支持展开/折叠
- **上传下载**：支持大文件（数 GB）上传/下载
  - 分片上传（1 MB 每片，ChaCha20-Poly1305 加密）
  - 断点续传、上传失败重试
  - 并发传输（默认 3 个并发任务）
- **文件操作**：创建目录、重命名、移动、删除（受权限控制）
- **文件预览**（支持多格式）：
  - 图片：png / jpg / gif / bmp / webp / tiff / svg / ico
  - 视频：mp4 / mkv / avi / mov / webm / flv / wmv / m4v / mpg / mpeg / ts / 3gp（流式零缓存播放）
  - 音频：mp3 / wav / ogg / flac / m4a / aac / wma / opus（流式零缓存播放）
  - 文本：txt / log / json / xml / yaml / ini / csv / Markdown / 代码文件（流式逐帧加载，首屏 ≤3s）
  - Markdown：渲染为 HTML，支持代码块高亮、表格、Mermaid 图表
- **流式预览（v1.1.0+）**：桌面端/移动端视频/音频通过本地 HTTP 代理零缓存播放；Web 端（v1.3.1+）通过 Service Worker 流式代理（`/jflove-stream/<token>`，解析 Range → 后端加密流逐帧解密 → 206 返回）真边下边播 + 拖动 seek，非安全上下文自动回退 MSE；文本通过 StreamTextLoader 逐帧流式加载，不写本地临时文件

### 📝 笔记本管理

- Markdown 编辑器 + 实时预览（GitHub 风格 CSS）
- 暗黑模式（跟随系统 `prefers-color-scheme`）
- 代码块语法高亮（highlight.js）+ Mermaid 图表渲染
- 笔记新建、编辑、保存、删除（直接操作服务端 .md 文件）
- 笔记搜索（文件名/内容）、标签、收藏
- 每个用户在设置页面独立选择笔记存放目录（互相不可见）
- 大纲面板（按标题层级解析）

### 👤 用户与权限管理（管理员功能）

- **用户管理**：添加/删除普通用户、修改密码、启用/禁用账号
- **磁盘管理**：添加/编辑/删除虚拟磁盘（映射服务端真实路径）
- **权限配置**：为每个用户配置虚拟磁盘的读/写/删除权限
- **安全宪法约束**：所有接口经过加密，JWT 通过加密 body 传递

### 🔄 目录同步

- 手动同步与自动同步两种模式（自动同步间隔可配置，最小 30 秒）
- 双向增量同步：本地多→上传；远端多→下载；两边都有→按 mtime 取新者（容差 2 秒）
- **永不自动删除文件**（删除安全约束）：同步过程不会触发任何删除操作
- 同步配置客户端本地存储（v1.1.6+），不同客户端各自独立
- 多客户端隔离：每台机器独立管理自己的同步配置
- **两端支持**：桌面端/移动端，均可配置与管理同步任务

### 🔐 安全通信

- **端到端加密**：所有 API 请求/响应（除明文白名单外）走 ChaCha20-Poly1305 加密信封
- **前向保密 (PFS)**：每次会话临时密钥交换（X25519 ECDH + HKDF-SHA256），会话密钥仅存内存
- **JWT 鉴权**：ES256 签名，通过加密 Body 传递，不走明文 `Authorization` 头
- **文件流加密**：下载/预览按 64 KB 分片独立加密，每帧 `[4B 长度][12B nonce][密文+16B tag]`
- **错误响应加密**：全局异常处理器（HTTPException / StarletteHTTPException / RequestValidationError / Exception 兜底）统一加密错误 detail
- **URL 不携带业务参数**：桌面端/移动端所有业务参数在加密 body 中传递；Web 端（v1.3.0+）因浏览器禁止 GET 携带 body，只读接口将加密信封放入 URL query（`?nonce=...&ciphertext=...`），**query 中仅含密文，不含任何明文业务参数**，防止 URL 泄漏

---

## 安全措施

> 详细安全宪法见 [`AGENTS.md §9`](AGENTS.md#9-安全宪法强制条款不可降级)。以下为摘要。

### 加密通道

| 环节 | 算法 / 协议 |
|------|------------|
| 密钥交换 | X25519 ECDH + HKDF-SHA256（盐 `b"jflove-v1"`，32B 派生密钥） |
| 数据加密 | ChaCha20-Poly1305（12 字节随机 nonce） |
| 身份认证 | JWT（ES256，加密 body 传递） |
| 密码哈希 | bcrypt |
| 文件流加密 | 64 KB 分片独立加密帧 `[4B 长度][12B nonce][密文+16B tag]` |

### 明文白名单（三条）

仅以下接口不加密，其余全部走 ChaCha20-Poly1305 加密信封：

- `GET /health` — 健康检查
- `POST /api/v1/auth/key-exchange` — 密钥交换（交换前无法加密）
- `GET /api/v1/auth/admin-exists` — 检查管理员是否存在

### 安全要求

- 服务端无长期身份密钥 / 公钥固定（pinning）——保持前向保密，无负担开源
- 敏感数据不记录日志（token / 密码 / session_key 严禁记录；filename / path 可截断或哈希化）
- 响应头不暴露文件名 / 路径 / 用户标识
- 受保护接口必须使用 JWT 鉴权，路径参数路由必须做权限校验

---

## 版本变化

[点击查看构建产物发布记录](https://github.com/js1688/jflove/releases)

> 以下记录从 v1.1.6 开始的版本变化。更早版本参见 `文档记录/需求文档/`。

### v1.3.1（当前版本）— Web 端视频/音频边下边播 + 流式 404 修复

| 类型 | 变更 |
|------|------|
| 📅 | 2026-08-04 |
| 🌐 Web 端 | 视频/音频预览实现**真边下边播**：Service Worker 流式代理（`/jflove-stream/<token>`，解析 Range → 后端 `/api/v1/files/stream` 加密流逐帧解密 → 206 返回，原生解码器接管）为主路径；非安全上下文回退 MSE；完整下载仅作最后兜底 |
| 🐛 修复 | `/stream` 404「文件不存在」：后端约定 `path=目录+filename`，Web 端曾传完整路径导致双重拼接，已在 `openEncryptedStream` 归一化 |
| 🔧 修复 | `parseStreamFrames` 单分片丢帧：meta 帧与数据帧同分片到达时先解析缓冲再读取 |
| ✅ 测试 | Web 端 vitest 51 通过；真实服务器 500GB/叶方 IMG_0443.MP4 验证边下边播 + 拖动 seek |

### v1.3.0 — Web 端首个版本 + 后端 CORS

| 类型 | 变更 |
|------|------|
| 📅 | 2026-08-02 |
| 🌐 Web 端 | **全新模块**（React + TypeScript + Vite + Tailwind CSS）：登录/文件浏览/预览/笔记/设置/管理/同步/传输全功能，PC 端侧边栏 + 移动端底部 TabBar 双布局；只读 GET 接口走 URL query 加密信封；HTTP 非安全上下文自动回退纯 JS 加密（`@noble/curves` X25519 + HKDF-SHA256） |
| 🔧 服务端 | 新增 CORS 中间件；版本号 1.1.6 → 1.3.0 |
| ✅ 测试 | Web 端 vitest 35 用例通过；容器冒烟登录页 + SPA fallback |

### v1.2.3 — 移动端传输任务 Tab + 同步修复 + BUG 修复累积（hotfix1~5）

| 类型 | 变更 |
|------|------|
| 📅 | 2026-07-21（首次） / 2026-07-28 ~ 07-29（BUG 修复累积） |
| 📱 移动端 | 底部导航重构：移除「首页」Tab，新增「传输任务」Tab（对齐桌面端），登录后重定向 `/files` |
| 📱 移动端 | 同步修复：`SyncPage` 配置同步到 `SyncEngineService`，立即同步按钮不再静默失败 |
| 🐛 hotfix1 | 文件管理体验 10 项：传输任务进页、列表刷新、`file_picker` 多选上传、移动到、写权限控制、取消/清除任务等 |
| 🐛 hotfix2 | 传输与同步 6 项：下载目录改外部存储 `Download/jflove/`、同步实际传输、上传改三步协议等 |
| 🐛 hotfix3 | 预览流式化 3 项：视频/音频 StreamProxy 边下边播+seek、文本流式加载、扩展名扩至 60+ |
| 🐛 hotfix4 | 自动登录 + 服务器地址预填充 2 项 |
| 🐛 hotfix5 | 视频流 `ExoPlaybackException`：StreamProxy 误用 `dio.post` 改 `dio.get` |
| ✅ 测试 | 移动端版本号 `1.2.3+3`；加密协议 `X-Encrypted-Stream: v1` 不变 |

### v1.1.6 — 同步配置客户端本地化

| 类型 | 变更 |
|------|------|
| 📅 | 2026-06-21 |
| ⚠️ | **Breaking change**：服务端与桌面端必须同步升级 |
| 🔧 服务端 | 移除 `sync_configs` 表及全部 6 个 CRUD 接口；新增 `POST /api/v1/sync/snapshot`（直接传 `disk_id`+`remote_path`） |
| 🖥️ 桌面端 | `sync_service.py` 全部改为本地 JSON 文件操作（`sync_configs.json`）；`sync_engine.py` 适配 str UUID ID 和新 snapshot API |
| 🔄 同步配置 | 每台客户端独立管理自己的同步配置（local_path / auto_sync / sync_interval 互不影响） |
| ✅ 测试 | 服务端 106 / 桌面端 98 全部通过 |

### v1.1.5 — 桌面端持久化存储修复

| 类型 | 变更 |
|------|------|
| 📅 | 2026-05-17 |
| 🔧 修复 | 桌面端持久化数据从 PyInstaller 临时目录（`_MEIPASS`）迁移到用户数据目录 |
| 🖥️ 桌面端 | 存储方式从 QSettings（Windows 注册表）改为 JSON 文件（`session.json` + `server_history.json`） |
| 📁 用户数据目录 | Windows: `%APPDATA%/JFLove/`、Linux: `~/.local/share/JFLove/`、macOS: `~/Library/Application Support/JFLove/` |
| 🔄 自动迁移 | 首次启动自动从旧位置迁移数据 |

### v1.1.4 — 补丁修复与体验优化

| 类型 | 变更 |
|------|------|
| 📅 | 2026-05-?? |
| 🔧 服务端 | `rename`/`move` 路径不存在时返回 404（原 400）；JWT 最大有效期扩展至 30 天 |
| 🖥️ 桌面端 | 服务端地址历史持久化修复（连接成功立即写入，重启/过期不丢失） |
| 🖥️ 桌面端 | Token 有效期选项从"按小时"改为"按天"：1 天 / 7 天 / 30 天 |
| 🐛 桌面端 | 修复 token 过期弹窗后出现双主窗口的必现 Bug |

---

## 文档与开发记录

| 类别 | 路径 |
|------|------|
| 需求文档 | [`文档记录/需求文档/`](文档记录/需求文档/) |
| 设计文档 | [`文档记录/设计文档/`](文档记录/设计文档/) |
| 后端开发记录 | [`文档记录/后端开发记录/`](文档记录/后端开发记录/) |
| 桌面端开发记录 | [`文档记录/桌面端开发记录/`](文档记录/桌面端开发记录/) |
| 移动端开发记录 | [`文档记录/移动端开发记录/`](文档记录/移动端开发记录/) |
| Web 端开发记录 | [`文档记录/Web端开发记录/`](文档记录/Web端开发记录/) |
| 代码审查报告 | [`文档记录/代码审查报告/`](文档记录/代码审查报告/) |
| 测试报告 | [`文档记录/测试报告/`](文档记录/测试报告/) |
| 项目管理记录 | [`文档记录/项目管理记录/`](文档记录/项目管理记录/) |
| 版本发布记录 | [`文档记录/版本发布记录/`](文档记录/版本发布记录/) |

---

## 快速导航

- 服务端技术文档 → [`jflove-server/README.md`](jflove-server/README.md)
- 桌面端技术文档 → [`jflove-desktop/README.md`](jflove-desktop/README.md)
- 移动端技术文档 → [`jflove-app/README.md`](jflove-app/README.md)
- Web 端技术文档 → [`jflove-web/README.md`](jflove-web/README.md)
- 项目工作手册 → [`AGENTS.md`](AGENTS.md)
- 数据库文件 → [`jflove-db/`](jflove-db/)
