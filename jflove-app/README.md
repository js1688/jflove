# jflove-app

JFLove 移动端 App（Android 首版），基于 Flutter + Dart + Riverpod 构建的私有文档与笔记协同管理应用。

> **当前版本：v1.5.0（`pubspec.yaml` 中 `version: 1.5.0+1005000`，`+` 后为 build number，由版本号派生）** — 版本号对齐服务端/桌面/Web 端。

> **v1.5.0** — UI 现代化改版 + Markdown Mermaid 图表渲染：新增**设计令牌体系**（`AppTokens` ThemeExtension，亮暗两套）与**三态主题**（跟随系统/亮色/暗色，设置页可切换并持久化）；13 个页面全部令牌化（硬编码 `Colors.*` 清零，加载态统一改骨架屏）；底部导航 **6 → 5**（修复中心并入传输页第二个页签，`/repair` 旧路径保留）；Markdown 中的 ` ```mermaid ` 代码块用离线 WebView **真渲染为图形**（零网络依赖，支持缓存与点击放大）。

---

## 技术架构

| 组件 | 选择 |
|------|------|
| 框架 | Flutter 3.44+ |
| 语言 | Dart 3.12+ |
| 状态管理 | Riverpod（flutter_riverpod 3.x，7 个 Provider 文件） |
| 路由 | go_router（13 条路由 + ShellRoute 底部导航 + 路由守卫） |
| HTTP | dio + 自研 HttpService（加密信封 + ECDH 重同步） |
| 加密 | pointycastle + x25519（ChaCha20-Poly1305 + X25519 ECDH + HKDF-SHA256） |
| 安全存储 | flutter_secure_storage（Android Keystore） |
| 流式代理 | `lib/utils/stream_proxy.dart`（本地 HTTP 代理，ExoPlayer 边下边播 + Range） |
| 图表渲染 | webview_flutter（本地 HTML 页 + 内联离线 mermaid bundle，零网络依赖） |
| Markdown | flutter_markdown + markdown（自定义 `MermaidBuilder`） |
| 测试 | flutter_test（54 个用例） |

### 设计令牌体系（v1.5.0）

```
config/design_tokens.dart   ← 颜色的唯一真源（AppTokens 亮/暗 + 间距/圆角/阴影/动效）
        │
        ├─→ config/theme.dart           Material 主题（显式 ColorScheme + 组件主题）
        ├─→ config/mermaid_theme.dart   图表主题色表（mermaid 不接受 var()）
        └─→ widgets/app_card.dart       统一组件取色（context.tokens）

providers/theme_provider.dart  ← 三态主题 + 安全存储持久化（runApp 前恢复，无闪屏）
```

**约定：`lib/` 下除白名单文件外不得出现颜色字面量**（`Color(0x…)` / 语义 `Colors.xxx`；
`Colors.transparent` / `Colors.white` 作为「无颜色」与渐变前景色豁免）。
由 `test/design_tokens_test.dart` 静态扫描强制，白名单仅 4 个文件（令牌本体、
Material 主题、图表色表、Markdown 高亮配色）。

### Riverpod 自动重试已关闭（重要）

`main.dart` 显式创建 `ProviderContainer(retry: (_, _) => null)`。Riverpod 3 默认
对失败的 Provider 自动重试 10 次（指数退避到 6.4s），这期间 `AsyncValue` 是
「loading + 已有 error」，`AsyncValue.when` 会一直走 loading 分支 —— 网络不通时
用户看到的是**永远转圈的骨架屏**而不是错误提示。关闭后失败立刻呈现 `ErrorState`，
由用户点「重试」再发起请求（`ErrorState(onRetry: ...)`）。回归用例见
`test/async_error_state_test.dart`。

### 通信架构

```
页面 → Riverpod Provider → Service → HttpService(加密层) → dio → 后端 API
                                    ├── crypto.dart (X25519/ChaCha20/HKDF)
                                    ├── stream_frame.dart (流式帧解析)
                                    └── session.dart (session_key 仅存内存)
```

### 支持的页面（13 个路由；首页已移除，`/` 重定向 `/files`，底部导航 5 项：文件/笔记/同步/传输任务/设置）

| 路由 | 页面 | 说明 |
|------|------|------|
| `/login` | 登录页 | 密钥交换 + 管理员初始化 + TTL 选择 + 历史记录（品牌渐变视觉） |
| `/` | 重定向 `/files` | 首页已移除 |
| `/files` | 磁盘列表 | 可写/只读徽标 |
| `/files/:diskId` | 文件浏览 | 列表/上传/下载/重命名/删除/预览 |
| `/files/preview` | 文件预览 | 图片/音视频/文本/**Markdown（含 Mermaid 图表）**全屏预览 |
| `/notes` | 笔记列表 | 搜索 + CRUD |
| `/notes/:noteId` | 笔记编辑 | 编辑/预览切换 + Markdown 工具栏 + **图表预览** + 未保存提示 |
| `/sync` | 同步 | 配置 CRUD + 统计概览 + 本地 JSON 存储 |
| `/settings` | 设置 | 安全状态 + **外观（主题切换）** + 账号 + 退出 + 管理入口 + 离线修复配置（admin） + 关于 |
| `/transfer` | 传输任务 | 页签一：传输进度 + 统计 + 状态 |
| `/repair` | 修复中心 | 兼容旧路径；实际落到传输页**页签二**（修复任务列表 + 验证播放 + 覆盖原文件） |
| `/admin/users` | 用户管理 | 添加/删除/密码/启用禁用（admin） |
| `/admin/disks` | 磁盘管理 | 添加/编辑/删除（admin） |
| `/admin/permissions` | 权限配置 | 磁盘权限矩阵（admin） |

> **导航收敛说明（v1.5.0）**：底部导航由 6 项收敛为 5 项，「修复中心」并入「传输」
> 页的第二个页签。`/repair` 路由**保留**并直接打开该页签，同时底部高亮归一化到
> 「传输任务」，避免旧书签/通知跳转白屏或高亮错位。回归用例见
> `test/navigation_test.dart`。

---

## 开发环境

### 前置条件

- Flutter SDK 3.44+（`D:\flutter\flutter_3.44.6-stable`）
- VSCode + Flutter 插件
- Android SDK（构建 APK 需要）

### 构建与测试

```bash
cd jflove-app

# 安装依赖
flutter pub get

# 静态分析（必须零 Error）
dart analyze lib/

# 运行测试（设计令牌/主题/导航/加密/模型/Widget/帧解析）
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
│   ├── main.dart              # 入口（ProviderContainer retry=null + 主题恢复 + 竖屏锁定）
│   ├── app.dart               # MaterialApp.router + 13 条路由 + 底部导航（5 项）
│   ├── config/                # 应用配置 + 设计令牌（design_tokens）/ 主题 / 图表主题
│   ├── models/                # 数据模型（7 个文件）
│   ├── providers/             # Riverpod 状态管理（7 个文件，含 theme_provider）
│   ├── services/              # 业务逻辑层（11 个 service + mermaid 渲染）
│   ├── pages/                 # 13 个页面（files/notes/sync/transfer/repair/settings/admin/login）
│   ├── widgets/               # 公共 UI 组件（app_card 统一卡片 / mermaid_block / empty_state …）
│   └── utils/                 # crypto/http_service/session/stream_frame/stream_proxy/markdown/…
├── assets/mermaid/            # 离线图表渲染资源（renderer.html + mermaid.bundle.js）
├── test/                      # 54 个测试用例（设计令牌/主题/导航/异步失败态/加密/模型/Widget）
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

三端（服务端/桌面端/移动端）加密互通已验证通过（`flutter test` 54/54 通过）。

---

## Markdown 与 Mermaid 图表渲染（v1.5.0）

Markdown 中的 ` ```mermaid ` 代码块会渲染成**真正的图形**（而不是代码框）：

| 环节 | 实现 |
|------|------|
| 离线包 | `assets/mermaid/mermaid.bundle.js`（3.3 MB 自包含 IIFE，版本锁定 **11.16.0**，与 Web/桌面端字节级一致） |
| 渲染页 | `assets/mermaid/renderer.html`（纯 ASCII 模板；`__JF_CODE__` 等占位符由 Dart 侧替换成**可直接使用的 JS 字面量**） |
| 渲染服务 | `lib/services/mermaid/mermaid_render_service.dart`（构建 HTML / 解析结果 / 磁盘缓存） |
| 图块组件 | `lib/widgets/mermaid_block.dart`（骨架 → 渲染 → 图形 / 错误卡，点击全屏查看） |
| 缓存键 | `sha256(源码 + 主题 + 引擎版本)` → 应用缓存目录，命中时直接显示、不创建 WebView |

**安全约束**（对齐 `AGENTS.md` §9）：

- 渲染页 `securityLevel: 'strict'`、`htmlLabels: false`，不加载任何远程资源；
- `NavigationDelegate` 只放行 `about:` / `data:`，其余导航一律 `prevent`；
- 回传 SVG 的展示 WebView **禁用 JavaScript**；
- 缓存键只用于文件名，日志不记录图表源码（只记哈希）。

**已知约束**：mermaid 的主题色是渲染时**烘焙进 SVG** 的（不是 SVG 内 CSS 变量），
所以切换亮/暗主题必须重新渲染 —— `MermaidBlock` 通过 `didChangeDependencies`
侦测 `brightness` 变化并重渲。
