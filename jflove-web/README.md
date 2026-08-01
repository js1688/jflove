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
# 构建镜像
docker build -t jflove-web:1.3.0 .

# 运行容器
docker run -p 8080:80 jflove-web:1.3.0

# 访问 http://localhost:8080
```

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
- **视频/音频流式播放**：当前版本使用简单的 `<video>`/`<audio>` 标签，流式加密播放需配合 Service Worker 实现（后续版本优化）
- **Markdown 预览**：当前使用简化渲染，完整渲染需接入 marked.js + highlight.js + Mermaid.js（依赖已声明在 package.json）
