# JFLove — 私有文档与笔记协同管理系统

![Python](https://img.shields.io/badge/Python-3.14%2B-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green) ![PySide6](https://img.shields.io/badge/PySide6-6.11.0-orange) ![Flutter](https://img.shields.io/badge/Flutter-3.27%2B-blue) ![Dart](https://img.shields.io/badge/Dart-3.6%2B-blue) ![React](https://img.shields.io/badge/React-18-blue) ![TypeScript](https://img.shields.io/badge/TypeScript-5.7-blue) ![Vite](https://img.shields.io/badge/Vite-6-purple) ![License](https://img.shields.io/badge/License-Proprietary-red)

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
- **流式预览（v1.1.0+）**：桌面端/移动端视频/音频通过本地 HTTP 代理零缓存播放；Web 端（v1.4.0+）统一 MSE 主路径（HTTP/HTTPS 均可用）真边下边播；损坏/非流式媒体可手动发起离线修复（v1.4.2+，ffmpeg 无损重封装产物落盘后播放）；文本通过 StreamTextLoader 逐帧流式加载，不写本地临时文件

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

## 版本号管理（单一来源）

> 版本号**唯一真相**是仓库根 `version.json`，其余位置全部由脚本派生/同步，杜绝「改一处漏一处」或「应用内显示旧版本号」。

```json
// version.json
{ "version": "1.4.2" }
```

| 模块 | 版本号位置（由脚本自动同步） |
|------|--------------------------|
| jflove-server | `src/main.py`（/docs 显示）+ `Dockerfile LABEL` |
| jflove-desktop | `src/config/settings.py` `APP_VERSION`（窗口/关于显示） |
| jflove-app | `pubspec.yaml` `version: x.y.z+code`（versionCode 由版本号派生）+ `lib/pages/settings/settings_page.dart`（「关于」页显示） |
| jflove-web | `package.json` + `src/config/constants.ts` `APP_VERSION`（设置页「关于」显示） |

> 移动端 versionCode 派生规则：`major*1000000 + minor*1000 + patch`（如 `1.4.2 → 1004002`），无需单独维护 build number。
> 三个 `build.py` 构建前校验模块内版本号与 `version.json` 一致，不一致直接中止。

### 改版本号（一条命令）

```bash
echo '{"version":"1.4.3"}' > version.json   # 或手动编辑
python scripts/sync_version.py               # 同步全部位置（含 pubspec 派生 versionCode）
```

---

## 打包构建指引（本地）

### 统一入口

根目录 `build.py` 是顶层编排器，一条命令完成「同步版本 → 选模块 → 环境检查 → 打包」：

```bash
python build.py                    # 交互式多选模块（如 1,3 / all / 回车=全部）
python build.py -m server,desktop  # 参数指定模块
python build.py -m all             # 全部模块
python build.py --no-sync          # 跳过版本号自动同步
python build.py --skip-env-check   # 跳过环境检查强行构建
```

流程：读 `version.json` → 自动同步版本号 → 选模块 → 逐模块环境检查（不满足则打印原因并跳过）→ 依次打包（desktop 自动切模块 venv）→ 汇总成功/跳过/失败。

### 各模块环境依赖与产物

| 模块 | 依赖（不满足自动跳过） | 产物 |
|------|----------------------|------|
| server | Docker | `jflove-server:<version>` 镜像 + `latest` |
| desktop | 模块 venv（PyInstaller + PySide6） | `jflove-desktop/build/dist/JFLove`（Linux）/ `JFLove.exe`（Windows） |
| web | Docker + `package-lock.json` | `jflove-web:<version>` 镜像 |
| app | Flutter + Android SDK | `build/app/outputs/flutter-apk/app-debug.apk` + `app-release.apk` |

### 完整打包流程

```bash
# 1. 改版本（唯一真相）
echo '{"version":"1.4.3"}' > version.json
# 2. 统一打包（自动同步版本 + 环境检查 + 打包）
python build.py -m all
# 3. 提交发版（打 tag 触发 CI 线上构建）
git add -A && git commit -m "chore: bump 1.4.3"
git tag v1.4.3 && git push origin main --tags
```

---

## GitHub Actions 线上打包（CI）

> 各端构建已迁移到 GitHub Actions 云端，解决「本地切机器」的强环境依赖（Docker / PySide6 / Flutter+Android SDK / Node）。
> **本地构建方式完整保留**（`python build.py` / `flutter build apk`），二者互不影响。

### 触发方式

- **手动触发**：仓库页 Actions → 选择对应 workflow → Run workflow
- **自动触发**：推送 `v*` 开头的 tag（如 `v1.4.2`）

### 各端 workflow 与产物

| Workflow | 构建内容 | 产物落点 |
|---|---|---|
| `build-server.yml` | 服务端 Docker 镜像（复用 `build.py` 的版本/DB 校验） | GHCR `ghcr.io/<owner>/jflove-server:<ver>` + `latest` |
| `build-web.yml` | Web 端 Docker 镜像（复用 `build.py`） | GHCR `ghcr.io/<owner>/jflove-web:<ver>` + `latest` |
| `build-desktop.yml` | 桌面端 PyInstaller（Linux + Windows） | Artifact：`JFLove` / `JFLove.exe` |
| `build-app.yml` | 移动端 APK（release，含 R8 禁令检查） | Artifact：`app-release.apk` |

### 产物如何获取

- **Docker 镜像**：CI 推送到 GitHub Container Registry，本地拉取：
  ```bash
  docker pull ghcr.io/<你的用户名>/jflove-server:1.4.2
  docker pull ghcr.io/<你的用户名>/jflove-web:1.4.2
  ```
  也可在仓库的 **Packages** 页查看已发布镜像。
- **桌面端 / 移动端产物**：进入对应 workflow 本次运行页，底部 **Artifacts** 区下载 zip。

### 版本号校验（CI 防呆）

CI 从 tag 提取版本号，校验 `tag == version.json` 与模块内 7 处版本号，任一不一致直接失败。发版前只需：

```bash
echo '{"version":"1.4.3"}' > version.json
python scripts/sync_version.py
git add -A && git commit -m "chore: bump 1.4.3"
git tag v1.4.3 && git push origin main --tags
```

---

## 版本变化

[点击查看构建产物发布记录](https://github.com/js1688/jflove/releases)

> 以下记录从 v1.1.6 开始的版本变化。更早版本参见 `文档记录/需求文档/`。

### v1.4.2（当前版本）— 手动离线媒体修复 + 播放路径纯净化 + seek 回归修复

| 类型 | 变更 |
|------|------|
| 📅 | 2026-08-29 |
| 🔧 服务端 | 架构调整：移除 time 实时修复流，`/stream` 只输出健康文件字节流；新增播放门禁（真损坏 415 + `[MEDIA_NEEDS_REPAIR]`，MKV/AVI/faststart MP4 等原生可播格式放行）；新增离线修复任务（6 个加密 API + `media_repair_tasks` 表 + asyncio 队列 worker，ffmpeg 无损重封装产物落盘 `.jflove-repair/`，支持取消/覆盖/删除）；`_moov_at_front` 放宽为 moov 前置即健康（修复 v1.4.1 卡顿重灾区）；隐藏目录 `.jflove-repair` 全路径防护 |
| 🐛 修复 | 代码审查 S-1：`repair_task_id` 产物流补磁盘读权限校验；M-1：`delete-record` 只读账号禁止删除他人记录 |
| ✅ 测试 | 后端 131 / 桌面 99 / 移动 24 / Web 53 全通过（后端新增 23 例） |
| 📦 发布 | 新增 `media_repair_tasks` 表（幂等 DDL + 回滚脚本）；桌面 JFLove.exe 244.5MB；移动 app-debug.apk 177MB |

### v1.4.1 — 视频播放修复（时长 + seek）

| 类型 | 变更 |
|------|------|
| 📅 | 2026-08-23 |
| 🔧 服务端 | 媒体探测失败回退 byte、ffmpeg 可用性修复；TS 伪装 MP4 强制转 AAC；faststart MP4 判定修正（moov+mvex）；fMP4 init 段补读；fMP4 mvhd duration 改写（供播放器显示总时长） |
| 🌐 Web 端 | MSE 非 fMP4 回退下载 + 下载进度；MSE 时长/seek 修复（timestampOffset + 非受控 src + AbortController 防竞态） |
| 🖥️ 桌面端 / 📱 移动端 | StreamProxy time 修复流声明 Accept-Ranges，使 QMediaPlayer / ExoPlayer 可拖拽 seek |
| ✅ 测试 | 后端 122 / 桌面 102 / 移动 27 / Web 52 全通过 |
| 📦 发布 | 镜像 server/web 推送腾讯云 CCR；桌面 JFLove.exe 242MB；移动 debug+release APK（1.4.1+5） |

### v1.4.0 — 媒体修复与行业标准边下边播（四端）

| 类型 | 变更 |
|------|------|
| 📅 | 2026-08-13 |
| 🔧 服务端 | 媒体修复服务：损坏/非流式媒体经 ffmpeg `-c copy` 重封装为 fMP4、stdout 管道直出（不落盘、不改原文件）；开关为服务端 config 三键（`media_repair_enabled`/`allow_transcode`/`max_concurrent`，默认关闭，C 端配置立即生效）；声明机制（仅带 `range_start_seconds` 的客户端走 time 修复，旧客户端零回归）；time meta 携带 `file_size`/`duration`/`codec` |
| 🌐 Web 端 | 边下边播统一 MSE 主路径（HTTP/HTTPS 均可用，删除 Service Worker）；修复流 codec 由服务端 meta 下发并组装完整 MIME；修复 loading 切换重建 video 元素与 StrictMode 双跑互扰 |
| 🖥️ 桌面端 | 设置页媒体修复开关（admin）；StreamProxy 适配 time 修复流（200+chunked 顺序流、字节 Range 线性映射时间 seek） |
| 📱 移动端 | 设置页媒体修复开关（admin）；StreamProxy 同样适配 time 修复流 |
| ✅ 测试 | 后端 120 / 桌面 102 / 移动 27 / Web 52 全通过；真实服务 E2E（mkv、损坏 mp4 边下边播）；桌面端打包冒烟通过 |
| 📦 发布 | 四端完整发布：桌面（JFLove.exe）+ 移动（app-debug.apk）2026-08-13；后端镜像 `jflove-server:1.4.0` + Web 镜像 `jflove-web:1.4.0` 2026-08-16 补发布 |

### v1.3.1 — Web 端视频/音频边下边播 + 流式 404 修复

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
