# jflove-app

JFLove 移动端 App（Android 首版），基于 Flutter + Dart + Riverpod 构建的私有文档与笔记协同管理应用。

> **当前版本：v1.2.1** — 同步管理交互体验优化。

---

## 技术架构

| 组件 | 选择 |
|------|------|
| 框架 | Flutter 3.27+ |
| 语言 | Dart 3.6+ |
| 状态管理 | Riverpod（flutter_riverpod，6 个 Provider 文件） |
| 路由 | go_router（14 条路由 + ShellRoute 底部导航 + 路由守卫） |
| HTTP | dio + 自研 HttpService（加密信封 + ECDH 重同步） |
| 加密 | pointycastle + x25519（ChaCha20-Poly1305 + X25519 ECDH + HKDF-SHA256） |
| 安全存储 | flutter_secure_storage（Android Keystore） |
| 测试 | flutter_test（22 个用例） |

### 通信架构

```
页面 → Riverpod Provider → Service → HttpService(加密层) → dio → 后端 API
                                    ├── crypto.dart (X25519/ChaCha20/HKDF)
                                    ├── stream_frame.dart (流式帧解析)
                                    └── session.dart (session_key 仅存内存)
```

### 支持的页面（14 个路由）

| 路由 | 页面 | 说明 |
|------|------|------|
| `/login` | 登录页 | 密钥交换 + 管理员初始化 + TTL 选择 + 历史记录 |
| `/` | 首页 | 用户信息 + 4 快捷入口 + 传输浮窗 |
| `/files` | 磁盘列表 | 可写/只读标记 |
| `/files/:diskId` | 文件浏览 | 列表/上传/下载/重命名/删除/预览 |
| `/files/preview` | 文件预览 | 图片/文本/Markdown 全屏预览 |
| `/notes` | 笔记列表 | 搜索 + CRUD |
| `/notes/:noteId` | 笔记编辑 | 编辑/预览切换 + Markdown 工具栏 + 未保存提示 |
| `/sync` | 同步 | 配置 CRUD + 本地 JSON 存储 |
| `/settings` | 设置 | 安全状态 + 账号 + 退出 + 管理入口 + 关于 |
| `/transfer` | 传输任务 | 进度条 + 统计 + 状态 |
| `/admin/users` | 用户管理 | 添加/删除/密码/启用禁用（admin） |
| `/admin/disks` | 磁盘管理 | 添加/编辑/删除（admin） |
| `/admin/permissions` | 权限配置 | 磁盘权限矩阵（admin） |

---

## 开发环境

### 前置条件

- Flutter SDK 3.27+（`D:\flutter\flutter_3.44.6-stable`）
- VSCode + Flutter 插件
- Android SDK（构建 APK 需要）

### 构建与测试

```bash
cd jflove-app

# 安装依赖
flutter pub get

# 静态分析（必须零 Error）
dart analyze lib/

# 运行测试（加密/模型/Widget/帧解析）
flutter test

# 构建 APK
flutter build apk --debug
# 产物: build\app\outputs\flutter-apk\app-debug.apk
```

---

## 目录结构

```
jflove-app/
├── lib/
│   ├── main.dart              # 入口（ProviderScope + 竖屏锁定）
│   ├── app.dart               # MaterialApp.router + 14 条路由 + 底部导航
│   ├── config/                # 应用配置 + 主题
│   ├── models/                # 数据模型（7 个）
│   ├── providers/             # Riverpod 状态管理（6 个文件）
│   ├── services/              # 业务逻辑层（11 个 service）
│   ├── pages/                 # 14 个页面
│   ├── widgets/               # 公共 UI 组件（5 个）
│   └── utils/                 # crypto/http_service/session/stream_frame/logger
├── test/                      # 22 个测试用例
├── android/                   # Android 原生壳
├── pubspec.yaml
└── README.md
```

---

## 加密协议

| 环节 | 算法 |
|------|------|
| 密钥交换 | X25519 ECDH + HKDF-SHA256（盐 `b"jflove-v1"`，`deriveKey` 正确用法） |
| 数据加密 | ChaCha20-Poly1305（12 字节随机 nonce） |
| 身份认证 | JWT（ES256，通过加密 Body 传递） |
| 文件流加密 | 64 KB 分片独立加密帧 [4B长度][12B nonce][密文+16B tag] |

三端（服务端/桌面端/移动端）加密互通已验证通过（`flutter test` 22/22 通过）。
