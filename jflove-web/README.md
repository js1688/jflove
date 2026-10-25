# jflove-web

JFLove 浏览器端 Web 应用，基于 React + TypeScript + Vite + Tailwind CSS 构建的个人数字工作台 Web 客户端：文件管理、笔记、媒体预览、传输任务与媒体修复。

支持 PC 端（侧边栏导航）和移动端（底部 TabBar 导航）双布局自适应。

> **当前版本：v1.8.2**（版本号唯一真相 = 仓库根 `version.json`；本模块由 `python scripts/sync_version.py`
> 同步两处：`package.json` 的 `version` 与 `src/config/constants.ts` 的 `APP_VERSION`。
> 改版本只改 `version.json` + 跑同步脚本，**不要手改任何一处版本号字段**；不存在 `build.py --version` 选项）

> **v1.8.2**：管理员「系统设置 → 离线下载」新增解析 / 连接 / 读取三项超时
> （默认 90/15/60 秒，允许 30~180 / 5~30 / 15~120，复用既有 `/api/v1/config` 加密接口，仅管理员可写）；
> 服务器目录选择器的超限文案改用 `server-dirs` 响应新增的 `limit`（旧服务端缺省回落 2000）。

---

## 技术架构

| 组件 | 用途 |
|------|------|
| **React 18** | UI 框架，函数式组件 + Hooks |
| **TypeScript 5.7** | 类型安全，严格模式 |
| **Vite 6** | 构建工具，HMR 热重载 |
| **Tailwind CSS 4** | 原子化 CSS，响应式布局 |
| **Zustand 5** | 轻量状态管理 |
| **React Router v7** | SPA 路由，布局路由 + 路由守卫 |
| **Web Crypto API** | X25519 ECDH + HKDF-SHA256 密钥交换（安全上下文主路径） |
| **@noble/ciphers** | ChaCha20-Poly1305 对称加密 |
| **@noble/curves + @noble/hashes** | 非安全上下文（HTTP 域名）纯 JS 回退：X25519 + HKDF-SHA256，协议不变 |

### 加密协议（三端统一）

| 环节 | 实现 |
|------|------|
| 密钥交换 | Web Crypto API X25519 ECDH |
| 密钥派生 | HKDF-SHA256（盐 `b"jflove-v1"`，32B） |
| 数据加密 | ChaCha20-Poly1305（12B 随机 nonce） |
| 加密信封 | `{"nonce":"<Base64>","ciphertext":"<Base64>"}` |
| 流式帧 | `[4B 大端长度][12B nonce][密文+16B tag]` |

> **只读（GET）接口传参方式**：浏览器禁止 GET 携带 body，Web 端只读接口将加密信封
> 放入 URL query（`?nonce=...&ciphertext=...`）发送；服务端 middleware 在请求体为空时
> 从 query 读取信封（全局通用，桌面/移动端仍走 body，互不影响）。query 中仅含密文，
> 不含任何明文业务参数。

> **非安全上下文兼容（HTTP 域名）**：Web Crypto API（`crypto.subtle`）仅存在于安全上下文
> （HTTPS / localhost）。当通过 `http://` 局域网域名访问时，Web 端自动回退到纯 JS 实现
> （`@noble/curves` X25519 + `@noble/hashes` HKDF-SHA256），**加密协议与互操作不变**；
> HTTPS / localhost 环境仍走 Web Crypto 主路径。

### 视频/音频预览：边下边播（v1.4.2 播放纯净化）

预览不落盘、边下边播，**不依赖 Service Worker，HTTP/HTTPS、内网/公网均可用**：

1. **MSE 主路径**（`media-source-player.ts`）：向后端 `/api/v1/files/stream`（v2 加密帧）拉流，
   逐帧解密后 append 到 MediaSource SourceBuffer，由浏览器解码播放：
   - **恒为 byte 模式**（v1.4.2 移除实时修复流）：健康文件按字节 range 直接流式；
   - **前向 seek 按需重拉**：MP4 家族文件拖到未缓冲区域时按字节偏移估算重拉
     （init 段 + moof 分片边界起的数据），失败时保持顺序下载渐进到达；
   - **损坏文件拒绝播放**：服务端返回 `[MEDIA_NEEDS_REPAIR]` → 弹「立即修复」引导。
2. **完整下载 → Blob**：仅当 MSE 不可用 / 容器不被支持时兜底（非主路径）。

### 媒体修复：手动离线修复（v1.4.2）

文件浏览右键「修复损坏媒体」+「修复中心」页面（任务列表/进度/取消/验证播放/
覆盖原文件）；修复产物落服务端原文件同目录 `.jflove-repair/` 隐藏目录，
覆盖时直接替换原文件（不留备份、二次确认）。任务列表全平台共享（所有登录
用户可见），操作要求磁盘写+删权限并存。修复并发数在「系统设置」配置（管理员可调）。

安全要点：流式 URL 仅含加密信封（`?nonce=...&ciphertext=...`），不暴露业务数据；
会话（session_key/JWT）仅存内存（sessionStorage），不进日志、不进 DOM。

构建注意：v1.4.0 起移除 Service Worker 流式代理（`src/sw/`、`src/utils/stream-proxy.ts` 已删除），
`vite build` 单入口输出，无 `sw.js`。

---

## 环境要求

- Node.js 22+
- npm 10+

## 安装与运行

```bash
cd jflove-web

# 安装依赖
npm install

# 开发模式（HMR 热重载，http://localhost:3000）
npm run dev

# 代码检查
npm run lint

# 运行测试
npm run test
```

## 构建

```bash
# 生产构建
npm run build

# 预览生产构建
npm run preview
```

### Docker 部署

```bash
# 方式一：统一入口（推荐，AGENTS.md §8）
# 仓库根目录执行，构建 jflove-web:<version> 本地镜像（先自动同步版本号）
python build.py -m web

# 方式二：直接 docker 构建
docker build -t jflove-web:1.5.1 .

# 运行容器
docker run -d --name jflove-web -p 8080:80 --restart=always jflove-web:1.5.1

# 访问 http://localhost:8080
```

> **镜像去向**：真实发布走 CI —— 推 `v*` tag 后由 `.github/workflows/build-web.yml` 构建并推送到
> `ghcr.io/<owner>/jflove-web:<ver>`（同时打 `latest`）；本地 `python build.py -m web` 只产出本地镜像。
> 模块脚本 `jflove-web/build.py` 的 `--tag` / `--no-cache` / `--save`（导出 `build/jflove-web-<version>.tar`）
> 仅供单步调试，**不作为交付路径**；版本号来自 `version.json`，**不存在 `--version` 选项**。

---

## 项目结构

```
jflove-web/
├── index.html                      # Vite HTML 入口
├── package.json                    # 依赖声明
├── tsconfig.json                   # TypeScript 配置
├── vite.config.ts                  # Vite 配置
├── eslint.config.ts                # ESLint 配置
├── build.py                        # Docker 镜像构建脚本（交付走仓库根 build.py）
├── Dockerfile                      # 多阶段 Docker 构建
├── nginx.conf                      # Nginx SPA 配置
├── README.md
├── src/
│   ├── main.tsx                    # 入口
│   ├── App.tsx                     # 根组件
│   ├── index.css                   # 全局样式
│   ├── styles/                     # 样式分层（tokens / theme / components）
│   ├── config/
│   │   ├── constants.ts            # 全局常量
│   │   └── routes.tsx              # 路由配置
│   ├── layouts/
│   │   ├── AppLayout.tsx           # 响应式切换
│   │   ├── DesktopLayout.tsx       # PC 端布局
│   │   ├── MobileLayout.tsx        # 移动端布局
│   │   └── AuthLayout.tsx          # 登录页布局
│   ├── pages/
│   │   ├── LoginPage.tsx           # 登录/管理员初始化
│   │   ├── FileListPage.tsx        # 虚拟磁盘列表
│   │   ├── DiskBrowserPage.tsx     # 磁盘文件浏览
│   │   ├── FilePreviewPage.tsx     # 文件预览
│   │   ├── NoteListPage.tsx        # 笔记列表
│   │   ├── NoteEditPage.tsx        # 笔记编辑/预览
│   │   ├── TransferTabsPage.tsx    # 传输/修复合并页（/transfer 与 /repair 的页签容器）
│   │   ├── TransferPage.tsx        # 传输任务视图（由 TransferTabsPage 引用）
│   │   ├── RepairCenterPage.tsx    # 修复中心视图（由 TransferTabsPage 引用）
│   │   ├── OfflineDownloadPage.tsx # 离线下载（任务中心 + 新建任务 + 我的默认目录）
│   │   ├── SecurityPage.tsx        # 安全状态
│   │   ├── SettingsPage.tsx        # 设置
│   │   └── admin/
│   │       ├── AdminUsersPage.tsx   # 用户管理
│   │       ├── AdminDisksPage.tsx   # 磁盘管理
│   │       ├── AdminPermissionsPage.tsx # 权限配置
│   │       └── AdminSystemPage.tsx  # 系统设置（媒体修复 / 离线下载 / 超时）
│   ├── components/
│   │   ├── PageHeader.tsx          # 页面标题栏（sticky 固定）
│   │   ├── PathBreadcrumb.tsx      # 路径面包屑（含返回上级箭头）
│   │   ├── ServerDirPickerModal.tsx # 服务器目录选择（v1.8.1；limit 文案 v1.8.2）
│   │   ├── DirTreeModal.tsx        # 目录树选择弹窗
│   │   ├── ConfirmDialog.tsx       # 确认对话框
│   │   ├── LoadingSpinner.tsx      # 加载指示器
│   │   ├── EmptyState.tsx          # 空状态
│   │   ├── ErrorBanner.tsx         # 错误提示
│   │   ├── offline/                # NewTaskPanel / OfflineSettingsModal（离线下载子视图）
│   │   ├── markdown/               # MarkdownRenderer / MermaidBlock / MermaidViewer
│   │   └── ui/                     # Button / Feedback / Icon / Overlay
│   ├── hooks/
│   │   ├── use-auth.ts             # 认证 Hook
│   │   ├── use-files.ts            # 文件操作 Hook
│   │   ├── use-notes.ts            # 笔记操作 Hook
│   │   └── use-responsive.ts       # 响应式断点（useIsPC / useBreakpoint）
│   ├── services/
│   │   ├── auth-service.ts         # 认证
│   │   ├── file-service.ts         # 文件管理
│   │   ├── note-service.ts         # 笔记管理
│   │   ├── repair-service.ts       # 媒体离线修复任务
│   │   ├── offline-download-service.ts # 离线下载（/api/v1/offline 的 10 个接口）
│   │   ├── user-service.ts         # 用户管理
│   │   ├── disk-service.ts         # 磁盘管理
│   │   ├── permission-service.ts   # 权限管理
│   │   ├── config-service.ts       # 服务端配置
│   │   └── server-history-service.ts # 地址历史
│   ├── stores/
│   │   ├── auth-store.ts           # 认证状态
│   │   ├── file-store.ts           # 文件浏览状态
│   │   ├── note-store.ts           # 笔记状态
│   │   ├── transfer-store.ts       # 传输任务状态
│   │   ├── settings-store.ts       # 设置状态
│   │   └── theme-store.ts          # 主题状态
│   ├── utils/
│   │   ├── crypto.ts               # 加密工具（Web Crypto + 纯 JS 回退）
│   │   ├── hash.ts                 # 哈希（Web Crypto）
│   │   ├── hash-fallback.ts        # 哈希纯 JS 回退
│   │   ├── http-client.ts          # 加密 HTTP 客户端
│   │   ├── session.ts              # 会话管理
│   │   ├── stream-frame.ts         # 流式帧解析
│   │   ├── media-source-player.ts  # MSE 边下边播主路径（v1.4.0+，HTTP/HTTPS 均可用）
│   │   ├── file-visual.ts          # 图片/媒体可视化辅助
│   │   ├── format.ts               # 格式化工具
│   │   ├── markdown.ts             # Markdown 解析辅助
│   │   ├── markdown-insert.ts      # Markdown 工具栏插入
│   │   ├── offline-download.ts     # 离线下载纯函数（快捷选择 / 终态 / 四态文案）
│   │   └── mermaid/                # 离线 Mermaid 渲染（loader / renderer / cache / sanitize）
│   └── types/
│       ├── models.ts               # 数据模型（含 v1.8.0 离线下载类型）
│       └── api.ts                  # API 类型
└── tests/
    ├── setup.ts
    ├── utils/                      # crypto / http-client / stream-frame / offline-download 测试
    ├── services/                   # server-history-service / offline-download-service 测试
    ├── stores/                     # auth-store / transfer-store 测试
    ├── layouts/                    # 导航与路由结构回归
    └── components/                 # PageHeader 组件测试
```

---

## 路由表

| 路径 | 页面 | 鉴权 | 布局 |
|------|------|------|------|
| `/login` | LoginPage | 无 | AuthLayout |
| `/` | 重定向 `/files`（首页已移除） | JWT | AppLayout |
| `/files` | FileListPage | JWT | AppLayout |
| `/files/:diskId` | DiskBrowserPage | JWT | AppLayout |
| `/files/:diskId/preview` | FilePreviewPage | JWT | AppLayout |
| `/notes` | NoteListPage | JWT | AppLayout |
| `/notes/:noteId` | NoteEditPage | JWT | AppLayout |
| `/transfer` | TransferTabsPage（页签一：传输任务） | JWT | AppLayout |
| `/repair` | TransferTabsPage（页签二：修复中心） | JWT | AppLayout |
| `/offline` | OfflineDownloadPage（任务中心 + 新建任务 + 我的默认目录） | JWT | AppLayout |
| `/security` | SecurityPage | JWT | AppLayout |
| `/settings` | SettingsPage | JWT | AppLayout |
| `/admin/users` | AdminUsersPage | admin | AppLayout |
| `/admin/disks` | AdminDisksPage | admin | AppLayout |
| `/admin/permissions` | AdminPermissionsPage | admin | AppLayout |
| `/admin/system` | AdminSystemPage（系统设置 / 媒体修复 / 离线下载） | admin | AppLayout |

---

## 已知限制

- **同步管理**：浏览器端**不提供**（v1.7.0 起移除该页面）——同步能力只在桌面端（云盘挂载）与移动端（相册备份）提供；访问旧 `/sync` 深链会落到兜底路由
- **视频/音频预览**：**MSE 主路径**（`src/utils/media-source-player.ts` 的 `playWithMSE`）边下边播 + 拖动 seek，**HTTP 与 HTTPS 均可用**（v1.4.0 起移除 Service Worker 流式代理，不再依赖安全上下文；单入口构建、无 `dist/sw.js`）；MSE 不可行时才回退「完整下载 → Blob」；超大文件（>500MB）提示下载，与桌面端/移动端一致
- **PDF 预览**：暂不支持（与桌面端/移动端一致），提示下载后使用本地程序打开
- **离线下载**：接口前缀 `/api/v1/offline`（10 个，全部加密信封、无路径参数）。任务列表/详情按账号隔离；默认目录按账号存在服务端（不写 localStorage，三端一致）。轮询间隔取 `/offline/meta` 的 `poll_interval_ms`（默认 2.5s），**页面切到后台或任务全部终态即停止**（AC-232）。无可写磁盘时整页显示「你没有可写入的磁盘，无法使用离线下载」并禁用新建任务；总开关关闭 / 未配置目录 / 目录失效分别有对应中文提示。总开关关闭时**侧栏与底部菜单入口不做置灰**（导航层拿不到 `/offline/meta`，避免每个页面多发一次请求），置灰提示落在页面内（AC-200 的页面内部分）。解析 / 连接 / 读取超时由管理员在「系统设置」修改（默认 90/15/60 秒，范围 30~180 / 5~30 / 15~120，保存后立即生效）
- **Markdown 预览**：已接入 `marked` + `DOMPurify`（XSS 清洗）+ `highlight.js`（代码高亮）+ `mermaid`（图表渲染），支持标题/加粗/斜体/列表/引用/代码块等
