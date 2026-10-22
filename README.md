# JFLove — 端到端加密的个人数字工作台

> 文件 · 笔记 · 媒体 · 多端同步 —— 一个自托管的私有空间。

![Python](https://img.shields.io/badge/Python-3.14%2B-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green) ![PySide6](https://img.shields.io/badge/PySide6-6.11.0-orange) ![Flutter](https://img.shields.io/badge/Flutter-3.44%2B-blue) ![Dart](https://img.shields.io/badge/Dart-3.12%2B-blue) ![React](https://img.shields.io/badge/React-18-blue) ![TypeScript](https://img.shields.io/badge/TypeScript-5.7-blue) ![Vite](https://img.shields.io/badge/Vite-6-purple) ![License](https://img.shields.io/badge/License-MIT-green)

JFLove 是一套**自托管**的个人数字工作台：把平时散落在各个网盘、笔记软件和手机相册里的东西，收拢到**你自己的那台机器**上——服务端一条 Docker 命令跑起来，桌面端、手机、浏览器三处随时读写，数据不经过任何第三方云。

它**不是"又一个网盘"或"又一个笔记应用"**：文件、笔记、媒体播放、本地文本编辑器、云盘挂载、相册备份共用同一套账号、同一套磁盘级读写权限、同一条端到端加密通道——一个账号决定谁能看哪个目录，各端看到的是同一份数据，通信全程 X25519 ECDH + ChaCha20-Poly1305 加密。

**适合谁**：有一台常开的机器（NAS / 小主机 / 云服务器），想自己掌握文件、笔记和影视资料；工作时拿它管代码之外的文档与素材，生活里拿它存照片、看片。

## 快速开始

```bash
# 1) 服务端：8989 端口；/data 存数据库，/storage 映射你的大容量磁盘
docker run -d --name jflove-server -p 8989:8989 --restart=always \
  -v /opt/jflove/data:/data \
  -v /mnt/big-disk:/storage \
  ghcr.io/js1688/jflove-server:<版本号>

# 2) Web 端：8080 端口，浏览器直接打开
docker run -d --name jflove-web -p 8080:80 --restart=always \
  ghcr.io/js1688/jflove-web:<版本号>
```

1. 打开 `http://<服务器地址>:8080`，填入服务端地址（如 `http://192.168.1.10:8989`）；系统尚无管理员时会引导**创建管理员账号**
2. 登录后在「磁盘管理」把 `/storage` 下的某个目录登记为**虚拟磁盘**，再给账号配好读 / 写 / 删权限
3. 桌面端 / 移动端从 [GitHub Releases](https://github.com/js1688/jflove/releases) 下载安装包（Windows `*-setup.exe`、Linux/Fedora `*.rpm`）与 Android APK，首次启动填同一个服务端地址即可登录

> 部署细节（多盘挂载、SELinux / Podman、环境变量）见 [`jflove-server/README.md`](jflove-server/README.md#docker-部署)。

---

## 开发环境

> 本项目为 **全 AI 自动化开发** 项目，从需求分析、架构设计、代码实现到测试发布，全部由 AI 模型在规范化流程驱动下完成。

### 开发工具链

| 工具 | 说明 |
|------|------|
| **操作系统** | Linux 7.0（开发主力）/ Windows / macOS（桌面端/iOS 构建） |
| **IDE** | **DeepSeek Harness**（主开发界面，本机 Web GUI `http://127.0.0.1:19387`） |
| **AI 工具** | DeepSeek Harness 原生技能 + 工作区指令（`.agents/skills/<角色名>/SKILL.md` + `AGENTS.md`），驱动多角色 AI 代理工作流 |
| **AI 模型** | **DeepSeek-V4.1-Flash**（provider `deepseek-account`，默认推理等级 high）；可选 provider `hsfz` 的 `deepseek-v4.1-flash` |
| **Node.js** | 22+ / npm 10+（Web 端构建与运行，Vite 6） |
| **代码风格** | EditorConfig 统一缩进（2 空格，UTF-8，LF 换行） |
| **调试配置** | `.vscode/launch.json` 预设服务端/桌面端/Web 端（Edge）启动配置（可选，用于本地单步调试） |

### AI 代理角色

项目采用 **多代理协作架构**，通过 `.agents/skills/` 目录下的技能文件（DSH 原生技能格式）驱动 10 个 AI 角色协同工作：

| 角色 | 职责 |
|------|------|
| **Product** | 需求挖掘、编写需求文档 |
| **Designer** | 系统架构、数据库、API 设计 |
| **Backend** | 后端业务代码实现（FastAPI） |
| **Desktop** | 桌面端 UI 与交互实现（界面为 **Web UI**：React + TS 渲染于 QtWebEngine；Python 侧仅原生外壳） |
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
| **jflove-desktop** | [`jflove-desktop/`](jflove-desktop/) | 跨平台桌面客户端（PySide6 原生外壳 + **Web UI**：React + TypeScript 渲染于 QtWebEngine） | ✅ 在研 |
| **jflove-app** | [`jflove-app/`](jflove-app/) | 跨平台移动端 App（Flutter + Dart），首版 Android-only，iOS/鸿蒙预留 | ✅ 在研 |
| **jflove-web** | [`jflove-web/`](jflove-web/) | 浏览器 Web 端（React + TypeScript），支持 PC 端与移动端浏览器双布局 | ✅ 在研 |

> **模块边界**：各模块独立开发，互不越界。详细代码规范与工作手册见 [`AGENTS.md`](AGENTS.md)。

---

## 功能特性

> **速览在前、细节在后**：一行对应下方一个小节（行序 = 小节序）。新增能力时两处**同一次**改完——表加一行、下方加一节；某端不支持就写 `—`，不要留空。

| 领域 | 能做什么 | 桌面 | 移动 | Web |
|------|---------|:----:|:----:|:---:|
| 📁 文档管理 | 虚拟磁盘映射真实目录、树形浏览、大文件分片上传 + 断点续传、并发传输、重命名 / 移动 / 删除；图片 / 视频 / 音频 / 文本 / Markdown 多格式预览与流式播放 | ✅ | ✅ | ✅ |
| 📝 笔记本管理 | Markdown 编辑 + 实时预览、代码高亮、Mermaid、搜索 / 标签 / 收藏、按人隔离的存放目录 | ✅ | ✅ | ✅ |
| ✏️ 文本编辑器（桌面端） | 纯本地多标签编辑器、12 项工具箱、29 项快捷键、跨标签查找替换 | ✅ | — | — |
| 👤 用户与权限管理（管理员功能） | 多账号、虚拟磁盘管理、磁盘级读 / 写 / 删权限 | ✅ | ✅ | ✅ |
| ☁️ 云盘挂载（桌面端） | 目录级挂载、按需缓存、增量同步（永不自动删远端） | ✅ | — | — |
| 📷 相册自动备份（移动端） | 仅 Wi-Fi / 仅充电、按年月分层、已备份去重 | — | ✅ | — |
| 🛠 媒体修复中心 | 损坏媒体离线无损重封装、任务列表三端共享 | ✅ | ✅ | ✅ |
| 📥 离线下载 | 贴无需登录的网页地址 → 分析视频 / 音频 / 图片清单 → 勾选下载到**该账号自己的磁盘目录**，产物直接进文件管理；移动端只读 | ✅ | ✅ | ✅ |
| 🔐 安全通信 | 端到端信封加密、前向保密、JWT(ES256)、错误响应同样加密、URL 不带明文参数 | ✅ | ✅ | ✅ |

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
- **流式预览**：桌面端/移动端视频/音频通过本地 HTTP 代理零缓存播放；Web 端统一 MSE 主路径（HTTP/HTTPS 均可用）真边下边播；损坏/非流式媒体可手动发起离线修复（ffmpeg 无损重封装产物落盘后播放）；文本通过 StreamTextLoader 逐帧流式加载，不写本地临时文件

### 📝 笔记本管理

- Markdown 编辑器 + 实时预览（GitHub 风格 CSS）
- 暗黑模式（跟随系统 `prefers-color-scheme`）
- 代码块语法高亮（highlight.js）+ Mermaid 图表渲染
- 笔记新建、编辑、保存、删除（直接操作服务端 .md 文件）
- 笔记搜索（文件名/内容）、标签、收藏
- 每个用户在设置页面独立选择笔记存放目录（互相不可见）
- 大纲面板（按标题层级解析；当前为**右侧可折叠竖栏**，预览/分屏下均可用）
- 打开笔记时的默认视图：**已有内容 → 预览模式**；空笔记（新建）→ 编辑模式；用户手动切换后本页不再自动改写

### ✏️ 文本编辑器（桌面端）

- **登录后可用**：入口在左侧菜单「文本编辑器」；**未登录时入口不可见、快捷键不响应**，从「打开方式」唤起文件会**先要求登录**，登录完成后自动打开
- **整页工作区 + 顶部标签页**：一个文档一个标签页，支持关闭（脏标签先询问）、**拖拽排序**、**溢出可及**；关闭最后一个标签页留在**空态**（新建 / 打开入口）
- **纯本地编辑**：打开 / 保存都针对本机文件，编辑器与远端**零交互**（不做远端读写、不依赖服务端）；**登录后断网 / 服务端未启动照常可用**
- **类型识别与差异化**：按扩展名识别语言、类型校验先行、标准保存语义（原子写）、状态栏显示编码 / 换行符 / 语言 / 行列与字符数
- **12 项工具箱**：查找 / 替换 / 跳转行、行操作、大小写转换、转义 / 反转义、Base64 编解码、JSON 格式 / 压缩 / 树、Markdown 编辑 / 预览 / 分屏、CSV / TSV 表格视图、SQL 美化、XML 格式化 / 压缩、两段文本对比、字号缩放 / 行号 / 自动换行。**每一项的首选动作都能直接执行**（执行后给中文结果提示并把变化处滚进视口）；**每一项都配有自己的快捷键**，且**只在编辑面板聚焦时生效**（焦点在别处时按键既不生效也不被吞）
- **查找 / 替换可跨标签页**：范围默认「当前标签页」，可切到「所有已打开的标签页」；结果按标签页分组列出（标题 + 行号 + 行摘要），点一条即跳到该标签页并选中命中文本；「全部替换」执行前给中文确认（将替换 N 处、涉及 M 个标签页），未挂载的标签页会明示数量且不参与
- **右击菜单**：编辑器可编辑区右击弹出**中文**标准编辑菜单（撤销 / 重做 / 剪切 / 复制 / 粘贴 / 全选，按上下文启用），**不含任何英文项与浏览器导航项**；工具栏 / 标签条 / 页面背景等非可编辑区右击**不再弹菜单**
- **Markdown 预览支持 Mermaid**：与笔记端**同一渲染栈**，` ```mermaid ` 渲染为矢量图，主题切换重渲，语法错误给中文提示且不白屏
- **多标签状态保持**：切换标签保留各自的光标位置、滚动位置、视图模式（编辑 / 预览 / 分屏 / 表格 / 树形）与脏标记
- **本机草稿与还原（登录后）**：自动暂存未保存编辑，**登录后**可还原上次的标签页（含顺序与激活项）；「清空草稿」入口可控，不影响已保存到磁盘的文件
- **快捷键可配置**：**29 项**（基础 18 项 + 工具箱 11 项，工具箱的「两段文本对比」沿用基础项），含「关闭当前标签页 `Ctrl+W`」（宿主占用时由应用接管）；设置页按作用域分**两组**——「应用级（任意页面生效）」与「文本编辑器级（仅编辑面板聚焦时生效）」，每行标注作用域，**组内同键拒绝保存**、跨组同键只提示不阻断；默认键**不占用** `Ctrl+Alt+*`，也**不与编辑器内核已占用的键位冲突**
- **文件关联**：可在设置页勾选扩展名并「设为默认」；界面已写清平台范围（Windows ✓ / Linux ✓（需自行在桌面环境选默认）/ macOS 暂不支持）

### 👤 用户与权限管理（管理员功能）

- **用户管理**：添加/删除普通用户、修改密码、启用/禁用账号
- **磁盘管理**：添加/编辑/删除虚拟磁盘（映射服务端真实路径）
- **权限配置**：为每个用户配置虚拟磁盘的读/写/删除权限
- **安全宪法约束**：所有接口经过加密，JWT 通过加密 body 传递

### ☁️ 云盘挂载（桌面端）

- **目录级挂载**：一个挂载点 = 一个远端子目录，可同时挂多个，**互不暴露**
- **文件级状态**：`已缓存` / `仅在线`；按需下载、释放空间（只删本地缓存）
- **增量同步**：服务端变更日志（`rev`）+ 全量对账兜底"绕过 API 的直接写入"
- 挂载点**默认关闭**，配置只存客户端本地；旧同步配置自动迁移
- **永不自动删除远端文件**

### 📷 相册自动备份（移动端）

- 设置页开关（**默认关闭**）+ 指定远端目录 + 仅 Wi-Fi / 仅充电
- 图片 / 视频可选，按 `年/月` 分层存放；已备份不重复上传
- 传输进度在「传输任务」页可见；关闭开关立即停止新上传
- **永不删除远端文件**（手机删照片不影响已备份内容）

### 🛠 媒体修复中心

- 损坏 / 不完整的媒体在播放时被拒绝（415），此时可**手动发起离线修复**（播放路径不跑 ffmpeg）
- 修复 = ffmpeg **无损重封装**（不重新编码），任务状态 `pending → running → verifying → success`
- 产物先落在原文件同目录的隐藏目录 `.jflove-repair/`，**验证能播后二次确认**才原子覆盖原文件（不留备份）
- 发起 / 覆盖要求磁盘**写 + 删**权限；任务列表三端共享，只读账号可看不可操作
- 并发上限由管理员配置（`media_repair_max_concurrent`，默认按 CPU 核数推导，硬上限 8）

### 📥 离线下载

- **输入**：只接受**无需登录**即可访问的网页地址；不支持登录态 / Cookie / DRM 流 / 直播
- **分析**：三级降级（站点解析 → 静态抓取 → 无头浏览器兜底，末级可由管理员全局关闭），产出可勾选清单；图片可「继续抓下一页」逐页追加
- **下载**：直链 / HLS / DASH / 批量图片；视频合并为**带音轨的 `.mp4`**、音频保留原格式不转码、多张图片落同名子目录，可选打包 zip
- **存放**：落到**你自己账号配置的**默认目录（必须是该账号有写权限的磁盘），完成后直接出现在「文档管理」；没有可写磁盘则该账号不可用
- **三端**：桌面端 / Web 端可新建与管理任务；移动端「离线下载中心」**只读**（列表 / 进度 / 详情）
- **配置**：管理员在「系统设置 → 离线下载」调整总开关 / 深度分析 / 并发 / 体积与项数上限（**默认开启**，可应急关闭）
- **出站安全**：服务端唯一的主动出站通道，逐跳校验目标（禁内网 / 禁回环）；解析器随服务端版本升级，**不做运行时在线自更新**

### 🔐 安全通信

- **端到端加密**：所有 API 请求/响应（除明文白名单外）走 ChaCha20-Poly1305 加密信封
- **前向保密 (PFS)**：每次会话临时密钥交换（X25519 ECDH + HKDF-SHA256），会话密钥仅存内存
- **JWT 鉴权**：ES256 签名，通过加密 Body 传递，不走明文 `Authorization` 头
- **文件流加密**：下载/预览按 64 KB 分片独立加密，每帧 `[4B 长度][12B nonce][密文+16B tag]`
- **错误响应加密**：全局异常处理器（HTTPException / StarletteHTTPException / RequestValidationError / Exception 兜底）统一加密错误 detail
- **URL 不携带业务参数**：桌面端/移动端所有业务参数在加密 body 中传递；Web 端因浏览器禁止 GET 携带 body，只读接口将加密信封放入 URL query（`?nonce=...&ciphertext=...`），**query 中仅含密文，不含任何明文业务参数**，防止 URL 泄漏

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
{ "version": "x.y.z" }
```

| 模块 | 版本号位置（由脚本自动同步） |
|------|--------------------------|
| jflove-server | `src/main.py`（/docs 显示）+ `Dockerfile LABEL` |
| jflove-desktop | `src/config/settings.py` `APP_VERSION`（原生外壳）+ **`ui/src/config/constants.ts`** `APP_VERSION`（界面「登录页页脚 / 设置-关于」显示） |
| jflove-app | `pubspec.yaml` `version: x.y.z+code`（versionCode 由版本号派生）+ `lib/pages/settings/settings_page.dart`（「关于」页显示） |
| jflove-web | `package.json` + `src/config/constants.ts` `APP_VERSION`（设置页「关于」显示） |

> 移动端 versionCode 派生规则：`major*1e6 + minor*1e3 + patch`（versionCode 无需单独维护 build number）。
> 共 **4 个构建脚本**：仓库根编排器构建前**自动同步**全部位置；server / desktop / web 三个模块 `build.py` 则在构建前**校验**模块内版本号与 `version.json` 一致，不一致直接中止；移动端**没有**模块 `build.py`（由根编排器直接调 `flutter build`）。

### 改版本号（一条命令）

```bash
echo '{"version":"x.y.z"}' > version.json   # 或手动编辑
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

> **桌面端的标准交付形态是安装包**（Windows `*-setup.exe` / Linux/Fedora `*.rpm`）——
> 无论从哪条路径触发（根入口 / 模块 `build.py` 裸命令 / 交互式多选 / CI）都以它为标准产出；
> onedir 目录与便携 zip 是附带产物，**单体 exe（`--mode onefile`）仅为兼容保留的显式选项**。
> 工具链缺失时构建会**降级但响亮报警**（缺 Inno Setup → 只出 zip；缺 rpmbuild → 只出 onedir），
> 降级产物不得当成标准交付。

### 各模块环境依赖与产物

| 模块 | 依赖（不满足自动跳过） | 产物 |
|------|----------------------|------|
| server | Docker | `jflove-server:<version>` 镜像 + `latest` |
| desktop | 模块 venv（PyInstaller + PySide6）；Windows 出安装包另需 **Inno Setup 6**（`ISCC.exe`） | **onedir 目录 + 安装包**：`build/dist/JFLove/` + Windows `JFLove-<version>-win64-setup.exe`、便携 `JFLove-<version>-win64-portable.zip`；Linux/Fedora `jflove-desktop-<version>-1.fc<N>.x86_64.rpm` |
| web | Docker + `package-lock.json` | `jflove-web:<version>` 镜像 |
| app | Flutter + Android SDK | `build/app/outputs/flutter-apk/app-debug.apk` + `app-release.apk` |

### 完整打包流程

```bash
# 1. 改版本（唯一真相）
echo '{"version":"x.y.z"}' > version.json
# 2. 统一打包（自动同步版本 + 环境检查 + 打包）
python build.py -m all
# 3. 提交发版（打 tag 触发 CI 线上构建）
git add -A && git commit -m "chore: bump x.y.z"
git tag vx.y.z && git push origin main --tags
```

---

## GitHub Actions 线上打包（CI）

> 各端构建已迁移到 GitHub Actions 云端，解决「本地切机器」的强环境依赖（Docker / PySide6 / Flutter+Android SDK / Node）。
> **本地构建方式完整保留**（统一走仓库根 `python build.py`，移动端也由它直接调 `flutter build apk`；**禁止**直接调各模块 `build.py` / `flutter build`），二者互不影响。

### 触发方式

- **手动触发**：仓库页 Actions → 选择对应 workflow → Run workflow
- **自动触发**：推送 `v*` 开头的 tag（如 `vx.y.z`）

### 各端 workflow 与产物

| Workflow | 构建内容 | 产物落点 |
|---|---|---|
| `build-server.yml` | 服务端 Docker 镜像（复用 `build.py` 的版本/DB 校验） | GHCR `ghcr.io/<owner>/jflove-server:<ver>` + `latest` |
| `build-web.yml` | Web 端 Docker 镜像（复用 `build.py`） | GHCR `ghcr.io/<owner>/jflove-web:<ver>` + `latest` |
| `build-desktop.yml` | 桌面端 **onedir + 安装包**（Windows job 装 Inno Setup；Linux job 用 Fedora 容器出 RPM） | Artifact：`JFLove-<ver>-win64-setup.exe` + `JFLove-<ver>-win64-portable.zip` / `jflove-desktop-<ver>-1.fc43.x86_64.rpm` |
| `build-app.yml` | 移动端 APK（release，含 R8 禁令检查） | Artifact：`app-release.apk` |

### 产物如何获取

- **Docker 镜像**：CI 推送到 GitHub Container Registry，本地拉取：
  ```bash
  docker pull ghcr.io/<你的用户名>/jflove-server:<版本号>
  docker pull ghcr.io/<你的用户名>/jflove-web:<版本号>
  ```
  也可在仓库的 **Packages** 页查看已发布镜像。
- **桌面端 / 移动端产物**：进入对应 workflow 本次运行页，底部 **Artifacts** 区下载 zip。

### 版本号校验（CI 防呆）

CI 从 tag 提取版本号，校验 `tag == version.json` 与模块内 9 处版本号（以 `scripts/sync_version.py::POSITIONS` 为准），任一不一致直接失败。发版前只需：

```bash
echo '{"version":"x.y.z"}' > version.json
python scripts/sync_version.py
git add -A && git commit -m "chore: bump x.y.z"
git tag vx.y.z && git push origin main --tags
```

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

- 版本发布记录 → [GitHub Releases](https://github.com/js1688/jflove/releases)（构建产物与校验值以 CI 为准）｜ 详细版 [文档记录/版本发布记录/](文档记录/版本发布记录/)
- 服务端技术文档 → [`jflove-server/README.md`](jflove-server/README.md)
- 桌面端技术文档 → [`jflove-desktop/README.md`](jflove-desktop/README.md)
- 移动端技术文档 → [`jflove-app/README.md`](jflove-app/README.md)
- Web 端技术文档 → [`jflove-web/README.md`](jflove-web/README.md)
- 项目工作手册 → [`AGENTS.md`](AGENTS.md)
- 数据库文件 → [`jflove-db/`](jflove-db/)
