# jflove-web

JFLove 浏览器端 Web 应用，基于 React + TypeScript + Vite + Tailwind CSS 构建的私有文档与笔记协同管理 Web 客户端。

支持 PC 端（侧边栏导航）和移动端（底部 TabBar 导航）双布局自适应。

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
| **Web Crypto API** | X25519 ECDH + HKDF-SHA256 密钥交换 |
| **@noble/ciphers** | ChaCha20-Poly1305 对称加密 |

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

### 视频/音频预览：边下边播（三档回退）

预览不落盘、边下边播，回退链（关键路径可靠）：

1. **Service Worker 流式代理（主路径）**：`<video src="/jflove-stream/<token>">` → SW 拦截 →
   解析 HTTP `Range` → 向后端 `/api/v1/files/stream`（v2，支持 range_start/range_end）拉加密帧 →
   逐帧解密 → `206 Partial Content` 返回，浏览器原生解码器接管。支持所有浏览器原生格式
   （mp4/webm/mp3/wav/flac/ogg/m4a/aac/opus），真边下边播 + 拖动 seek。
   **仅安全上下文可用（HTTPS / localhost / 127.0.0.1）**；生产部署建议 HTTPS。
2. **MSE 回退**：SW 不可用（HTTP 局域网）时，对 fMP4 / WebM / MP3 / FLAC / OGG 仍可边下边播
   （`media-source-player.ts`，按格式正确探测 codec，首帧 append 失败快速回退）。
3. **完整下载 → Blob**：仅当两者都不可行时（浏览器能力极限），非主路径。

安全要点：流式 URL 仅含不透明一次性 token（不暴露业务数据）；会话（session_key/JWT）经
postMessage 同步到 SW 内存、不落盘不进日志；登出时清空 SW 内存密钥。

构建注意：`vite build` 输出 `dist/sw.js`（SW 独立入口）；nginx SPA fallback 已兼容 `/sw.js`。
dev 下由 `vite.config.ts` 的 `jflove-sw-dev` 插件托管 `/sw.js` 并附 `Service-Worker-Allowed: /`。

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
# 方式一：一键构建脚本（推荐，对标服务端 build.py）
python build.py                      # 构建 jflove-web:1.3.0 本地镜像
python build.py --save               # 构建后导出 build/jflove-web-1.3.0.tar 离线包
python build.py --tag 1.3.0-rc1      # 自定义 tag
python build.py --no-cache           # 不使用 Docker 缓存

# 方式二：直接 docker 构建
docker build -t jflove-web:1.3.0 .

# 运行容器
docker run -d --name jflove-web -p 8080:80 --restart=always jflove-web:1.3.1

# 访问 http://localhost:8080
```

> 镜像仅构建到本地，推送到镜像仓库由人工执行（如 `docker push registry/jflove-web:1.3.0`）。

---

## 项目结构

```
jflove-web/
├── index.html                      # Vite HTML 入口
├── package.json                    # 依赖声明
├── tsconfig.json                   # TypeScript 配置
├── vite.config.ts                  # Vite 配置
├── eslint.config.ts                # ESLint 配置
├── Dockerfile                      # 多阶段 Docker 构建
├── nginx.conf                      # Nginx SPA 配置
├── README.md
├── src/
│   ├── main.tsx                    # 入口
│   ├── App.tsx                     # 根组件
│   ├── index.css                   # 全局样式
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
│   │   ├── HomePage.tsx            # 首页仪表盘
│   │   ├── FileListPage.tsx        # 虚拟磁盘列表
│   │   ├── DiskBrowserPage.tsx     # 磁盘文件浏览
│   │   ├── FilePreviewPage.tsx     # 文件预览
│   │   ├── NoteListPage.tsx        # 笔记列表
│   │   ├── NoteEditPage.tsx        # 笔记编辑/预览
│   │   ├── SyncPage.tsx            # 同步说明+引导
│   │   ├── TransferPage.tsx        # 传输任务
│   │   ├── SecurityPage.tsx        # 安全状态
│   │   ├── SettingsPage.tsx        # 设置
│   │   └── admin/
│   │       ├── AdminUsersPage.tsx   # 用户管理
│   │       ├── AdminDisksPage.tsx   # 磁盘管理
│   │       └── AdminPermissionsPage.tsx # 权限配置
│   ├── components/
│   │   ├── PageHeader.tsx          # 页面标题栏
│   │   ├── PathBreadcrumb.tsx      # 路径面包屑
│   │   ├── DirTreeModal.tsx        # 目录树选择弹窗
│   │   ├── ConfirmDialog.tsx       # 确认对话框
│   │   ├── LoadingSpinner.tsx      # 加载指示器
│   │   ├── EmptyState.tsx          # 空状态
│   │   └── ErrorBanner.tsx         # 错误提示
│   ├── hooks/
│   │   ├── use-auth.ts             # 认证 Hook
│   │   ├── use-files.ts            # 文件操作 Hook
│   │   ├── use-notes.ts            # 笔记操作 Hook
│   │   └── use-responsive.ts       # 响应式断点
│   ├── services/
│   │   ├── auth-service.ts         # 认证
│   │   ├── file-service.ts         # 文件管理
│   │   ├── note-service.ts         # 笔记管理
│   │   ├── sync-service.ts         # 同步（降级）
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
│   │   └── settings-store.ts       # 设置状态
│   ├── utils/
│   │   ├── crypto.ts               # 加密工具
│   │   ├── http-client.ts          # 加密 HTTP 客户端
│   │   ├── session.ts              # 会话管理
│   │   └── stream-frame.ts         # 流式帧解析
│   └── types/
│       ├── models.ts               # 数据模型
│       └── api.ts                  # API 类型
└── tests/
    ├── setup.ts
    ├── utils/crypto.test.ts
    └── components/PageHeader.test.tsx
```

---

## 路由表

| 路径 | 页面 | 鉴权 | 布局 |
|------|------|------|------|
| `/login` | LoginPage | 无 | AuthLayout |
| `/` | HomePage | JWT | AppLayout |
| `/files` | FileListPage | JWT | AppLayout |
| `/files/:diskId` | DiskBrowserPage | JWT | AppLayout |
| `/files/:diskId/preview` | FilePreviewPage | JWT | AppLayout |
| `/notes` | NoteListPage | JWT | AppLayout |
| `/notes/:noteId` | NoteEditPage | JWT | AppLayout |
| `/sync` | SyncPage | JWT | AppLayout |
| `/transfer` | TransferPage | JWT | AppLayout |
| `/security` | SecurityPage | JWT | AppLayout |
| `/settings` | SettingsPage | JWT | AppLayout |
| `/admin/users` | AdminUsersPage | admin | AppLayout |
| `/admin/disks` | AdminDisksPage | admin | AppLayout |
| `/admin/permissions` | AdminPermissionsPage | admin | AppLayout |

---

## 已知限制

- **同步管理**：浏览器沙箱限制，不支持本地文件系统双向同步（降级为展示+引导）
- **视频/音频预览**：Service Worker 流式代理**边下边播 + 拖动 seek**（等价桌面/移动端 StreamProxy，需 HTTPS/localhost 部署）；非安全上下文（HTTP 域名）自动回退「完整下载 → Blob」；超大文件（>500MB）提示下载，与桌面端/移动端一致
- **PDF 预览**：暂不支持（与桌面端/移动端一致），提示下载后使用本地程序打开
- **Markdown 预览**：已接入 `marked` + `DOMPurify`（XSS 清洗），支持标题/加粗/斜体/列表/引用/代码块等；代码高亮与 Mermaid 图表渲染为后续增强项
