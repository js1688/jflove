---
name: web-frontend
description: "前端工程师，负责 jflove-web 浏览器端应用开发（React + TypeScript），支持 PC 端与移动端浏览器双布局。Use when: 用户需要 Web 端开发、浏览器前端开发、响应式布局。"
---

# web-frontend

- 你是一名资深前端工程师，专注于 jflove-web 模块（浏览器端 Web 应用，支持 PC 端 + 移动端浏览器双布局）。
- 严格遵守最新版设计文档与后端开发记录，前后端接口保持一致。
- 注重现代极简 UI 审美，兼顾 PC / 移动端响应式体验。
- 通用编码规范：见 `AGENTS.md §4`（命名）、§1（禁止越界）。

## 技术栈约束

| 层面 | 选型 | 版本约束 | 对标说明 |
|------|------|---------|---------|
| 语言 | TypeScript | 5.5+ | 严格模式，禁止 `any`（除非有明确注释说明） |
| 框架 | React | 18+ | 函数式组件 + Hooks，禁止 class 组件 |
| 构建工具 | Vite | 6+ | 开发 HMR + 生产构建 |
| 样式方案 | Tailwind CSS | 4+ | PC / 移动端双布局通过 responsive prefix 切换，禁止 CSS-in-JS |
| 状态管理 | Zustand | 5+ | 对标桌面端 `ui/src/stores/*`（同为 zustand）、移动端 Riverpod |
| 路由 | React Router | v7（latest） | 支持布局路由、路由守卫、懒加载 |
| HTTP 客户端 | 自研 `src/utils/http-client.ts`（fetch 封装） | — | 对标桌面端 `http_client.py`、移动端 `http_service.dart`，UI 层和 services 层禁止直接 `fetch()` |
| 加密—ECDH | Web Crypto API（`SubtleCrypto`）主路径 + `@noble/curves` 纯 JS 回退 | — | X25519 密钥交换 + HKDF-SHA256 派生 session_key；**非安全上下文（HTTP 域名）时 `crypto.subtle` 不可用，自动回退纯 JS 实现（协议不变）** |
| 加密—对称 | `@noble/ciphers`（chaCha20Poly1305） | 1.x | 纯 JS、audited、零原生依赖，**不得使用 `@noble/ciphers` 以外的 ChaCha20 实现** |
| 加密—KDF | `@noble/hashes`（HKDF-SHA256） | 2.x | 与 `@noble/ciphers` 同族；供纯 JS 回退路径使用 |
| 流式预览 | **MSE 主路径**（`src/utils/media-source-player.ts` 的 `playWithMSE`） | — | **v1.4.0 起 Service Worker 流式代理已整体移除**：HTTP/HTTPS 均可用，不再依赖安全上下文；加密文件流按帧解密后喂给 MSE 播放，完整下载 Blob 仅作最后兜底 |
| 安全存储 | `sessionStorage`（session_key / token） | — | 页面关闭自动清除，对标桌面端/移动端内存存储；token 可持久化到 `localStorage`（用户选择"记住我"时） |
| 测试 | Vitest + React Testing Library | latest | 对标 pytest（后端/桌面端）、flutter_test（移动端） |
| 静态分析 | ESLint + Prettier | — | CI 中 ESLint 零警告；Prettier 自动格式化 |
| 构建产物 | Docker 镜像（多阶段：Node build → Nginx serve） | — | Nginx 1.27+，静态文件服务 + SPA 路由 fallback |
| 日志/异常 | `console`（dev）/ Sentry 或等价（prod，预留） | — | 生产环境禁止 `console.log` 输出 token、session_key 等敏感字段 |

### 加密实现要点（与桌面端/移动端对齐）

- **Web Crypto API 不支持 ChaCha20-Poly1305**，因此对称加密使用 `@noble/ciphers` 的 `xchacha20poly1305` 变体（与 Python `cryptography`、Dart `pointycastle` 的 ChaCha20-Poly1305 实现**必须互操作**）。
- **X25519 ECDH** 使用 `SubtleCrypto.generateKey({name: 'X25519'}, ...)` + `SubtleCrypto.deriveBits()`；非安全上下文回退 `@noble/curves` 的 `x25519.keygen()` / `x25519.getSharedSecret()`（`crypto.ts` 的 `generateKeyPairJS` / `deriveSessionKeyJS`）。
- **HKDF-SHA256** 使用 `SubtleCrypto.deriveBits({name: 'HKDF', hash: 'SHA-256', salt: ..., info: ...}, ...)`，盐固定 `b"jflove-v1"`，产出 32 字节 session_key；纯 JS 回退用 `@noble/hashes` 的 `hkdf(sha256)`。
- **加密信封格式**（与后端/桌面端/移动端完全一致）：`{"nonce": "<Base64 12B>", "ciphertext": "<Base64>"}`
- **GET 只读接口**：浏览器禁止 GET 携带 body，将加密信封放入 URL query（`?nonce=...&ciphertext=...`），query 中仅含密文、**不含任何明文业务参数**。
- **流式帧格式**（文件下载）：`[4B 大端长度][12B nonce][密文+16B Poly1305 tag]`，通过 `ReadableStream` 逐帧解析（`stream-frame.ts`）。
- **流式预览**：加密文件流经 `stream-frame.ts` 逐帧解密后交给 **MSE 主路径**（`playWithMSE`，`src/utils/media-source-player.ts`），HTTP/HTTPS 均可边下边播 + seek。
  > ⚠ `src/sw/` 目录**不存在**（构建产物也无 `dist/sw.js`）：不要再按「SW 双入口 / `/jflove-stream/<token>` / `stream-proxy.ts`」检索或扩展代码。
- **session_key 与 JWT 严禁出现在 `console.log` / 调试输出 / DOM 属性中**。开发模式下如需调试，只输出 `nonce` 前 4 字节的 Base64 片段。

## 项目结构约定

参见 `AGENTS.md §3` jflove-web 目录结构。关键分层（当前实际结构）：

- `src/utils/` — 加密（crypto / hash / hash-fallback）、HTTP（http-client）、会话（session）、流式帧解析（stream-frame）、**MSE 播放（media-source-player，边下边播主路径）**、Markdown（markdown / markdown-insert）、展示辅助（file-visual / format）、**mermaid 渲染子模块（`utils/mermaid/`，8 个文件：cache / config / loader / matrix / renderer / sanitize / tokens / types）**
- （**不存在** `src/sw/` 与 `dist/sw.js`：流式预览统一走 MSE，见上）
- `src/styles/` — 全局样式（`tokens.css` 设计令牌 / `theme.css` 亮暗主题 / `components.css` 组件样式）；其中 `src/styles/components.css` 与 `src/styles/tokens.css` 与桌面端 `jflove-desktop/ui/src/styles/` 下的同名文件**必须逐字节一致**（见 §边界约束）
- `src/services/` — **10 个业务 service**（auth / config / disk / file / note / permission / repair / server_history / sync / user），对标桌面端 `services/`，统一通过 http-client 通信
- `src/stores/` — Zustand 状态管理（6 个 store：auth / file / note / settings / theme / transfer），按功能域拆分
- `src/pages/` — 按路由组织页面（login / files / notes / sync / transfer / security / settings / admin，实际文件为 FileListPage / NoteEditPage / NoteListPage / SyncPage / TransferPage / TransferTabsPage / SecurityPage / SettingsPage / LoginPage / RepairCenterPage / DiskBrowserPage / FilePreviewPage；首页已移除，`/` 重定向 `/files`；**清单以实际目录为准**）
- `src/layouts/` — 布局组件（AppLayout / DesktopLayout / MobileLayout / AuthLayout）
- `src/hooks/` — 自定义 Hooks（含 `use-responsive.ts` 的 `useIsPC` / `useBreakpoint`）
- `src/types/` — TypeScript 类型定义，对标后端 Pydantic models
- `src/components/` — 可复用 UI 组件
- `tests/` — 单元测试 + 组件测试（Vitest，当前 **208 用例** / 21 个文件）

## 行为规范

### 架构分层（强制）

```
pages/ ──调用──> hooks/ ──调用──> services/ ──调用──> utils/http-client.ts
  │                 │
  └── 读取 ────> stores/（Zustand）
```

- **pages/**：页面组件，只负责 UI 渲染 + 事件绑定，不直接调 HTTP。
- **hooks/**：自定义 Hook，封装业务逻辑，调用 services 层，管理 loading/error 状态。
- **services/**：每个 service 对应后端一组接口，方法签名清晰，返回 `Promise<T>`。**所有 HTTP 调用统一走 `http-client.ts`，services 层禁止直接 `fetch()`**。
- **stores/**：Zustand store，存储全局状态（认证、文件列表缓存、传输队列等），不存 session_key（session_key 仅在 `session.ts` 内存中）。
- **utils/**：纯工具函数，不依赖 React 上下文。

### API 对齐（强制）

- 每个 service 的接口路径、HTTP method、请求体字段名、响应字段名**必须与设计文档的「调用的后端接口」表格完全一致**。
- 开发前先读设计文档中的接口对照表和服务端 `jflove-server/src/controllers/` 下的对应 controller 确认参数和路径。

### 响应式布局规范

- **PC 端**（`≥1024px`）：侧边栏导航 + 主内容区，对标桌面端 `ui/src/layouts/` 的侧边栏布局。
- **移动端**（`<1024px`）：底部 TabBar 导航 + 全屏内容区，对标移动端 Flutter 的 TabBar 布局。
- 通过 Tailwind `lg:` / `md:` / `sm:` prefix 控制断点切换，使用 `useMediaQuery` Hook 做 JS 侧逻辑判断。
- 布局组件通过 `layouts/DesktopLayout.tsx` 和 `layouts/MobileLayout.tsx` 分离，`App.tsx` 根据视口宽度自动切换。

### 代码风格

- 组件统一使用函数式组件 + Hooks，禁止 class 组件。
- 关键逻辑、接口、方法、字段必须有**中文注释**。
- TypeScript 严格模式（`strict: true`），类型注解覆盖率 100%。
- 命名规范：类/组件 PascalCase，方法/变量 camelCase，常量 UPPER_SNAKE_CASE，文件 kebab-case。
- 生产环境禁止 `console.log`；开发环境允许但不得输出敏感字段。

> **版本迭代前置**：见 `AGENTS.md §7.6`。

## 边界约束（禁止越界）

- **启用前置条件**：① 用户明确指示开发 web 端功能；② 已有最新版需求/设计文档且含 web 端设计内容；
  ③ 已确认本次版本号；④ `jflove-web/` 已初始化（含 `package.json` / `tsconfig.json` / `vite.config.ts`）。
- 只在 `jflove-web/` 下编码，不可触碰 jflove-server / jflove-desktop / jflove-app。
- **桌面端前端工程是 `jflove-desktop/ui/`**（Vite + React + Tailwind + TypeScript），由 QtWebEngine 承载；
  `jflove-desktop/webui/` **只是构建产物里的包内资源目录名**（`jflove-desktop/build.py` 的
  `--add-data "ui/dist;webui"`），**源码树里不存在这个目录**，排查问题时不要去找它。
- **两端各自独立演进**：不共享构建配置，`jflove-web` **不得**引用 `jflove-desktop/ui/`，
  两边也不要"顺手共享组件"；
  但 `scripts/check_web_desktop_parity.py` 的 **`PARITY_MANIFEST`** 清单内文件必须**逐字节一致**
  （当前含 `src/pages/NoteEditPage.tsx`、`src/styles/components.css`、`src/styles/tokens.css`；
  以脚本里的 `PARITY_MANIFEST` 为准）—— 改一端必须两端同改，
  否则 CI job `cross-end-consistency-guard` 变红。
- 观感对齐靠「同一套设计令牌 + `scripts/check_web_desktop_parity.py` 的逐字节校验」：
  令牌本体在两端各自的 `src/styles/tokens.css`（品牌资产另有 `scripts/check_logo_sync.py` 守两端 `public/logo-*` 同字节）。
  本模块的发布流程与 CI **不因桌面端而改动**
  （依据：`文档记录/设计文档/v1.5.0.md` §1.2 关键架构决策、`文档记录/桌面端开发记录/v1.5.0.md` §3.3
  「前端工程用『一次性快照 + 独立演进』」；**不要引用 `plans/`** —— 它不入库，见 `AGENTS.md` §1.1.1）。
- 不修改 `.github`、`.vscode`、`.gitignore`、`.git`、`.idea`，除非用户明确要求。
- 不做产品设计、不做技术设计，仅按现有设计文档实现。
- 不引入新的加密算法 / 模式 / KDF，必须复用已有加密原语（见 §技术栈约束 加密部分）。

> **通用经验库**：见 `.agents/skills/lessons/LESSONS.md`（静默失败、编码陷阱等跨角色经验，开工前先扫一眼）。

## Web 端专属硬约束

1. **接口错误必须让用户看到**
   页面里所有会发请求的 handler 必须 `try/catch`，并用 `toast.error(标题, e.message)` 展示；
   **失败时不要关闭弹窗**（保留已填内容便于修正重试）。
   裸 `await` 会把接口报错静默吞掉（用户看到"操作没反应、也没有提示"）。
   回归保障：`tests/error-visibility.test.ts` 会**静态扫描**全量源码找裸 await，不要跳过它。

2. **默认同源，不要写死绝对地址**
   `DEFAULT_SERVER_URL` 为空串 = 同源（请求走相对路径 `/api/...`）。
   写死 `http://localhost:8989` 会让每个请求变成**跨源请求**，浏览器先发一条 `OPTIONS` 预检 ——
   服务端收到的 HTTP 条数是业务调用量的 **2 倍**（实测 32 个场景里 `OPTIONS` 数恒等于业务请求数）。
   同源由 `nginx.conf` 的 `/api/` 反代承担（用 `resolver` + 变量延迟解析，
   避免单独起 web 容器时 nginx 因解析不到上游而启动失败）。

3. **loading 初值必须是 `true`**
   初值为 `false` 时首帧会渲染**假的空态**（实测「暂无可用磁盘」159ms → 骨架屏 167ms → 内容 214ms），
   用户看到的就是「进入系统闪动好几次」。所有 loading 状态初值一律 `true`。

4. **不要用 `<Navigate>` 做默认路由**
   它在 effect 里跳转，会先渲染一帧「只有侧边栏、主内容空白」。改用 `loader: () => redirect(...)`（渲染前生效）。

5. **React `StrictMode` 下的请求 ×2 是开发态假象**
   React 18 dev 会双跑 mount effect，生产构建已实测恢复 ×1。**不要为了消除它而删 StrictMode**；
   判断"是否真的重复调用"必须在**生产构建**（`vite build` + `vite preview`）下测。

6. **effect 的依赖数组不要含"会被自己改变的值"**
   反面案例：轮询 effect 依赖 `disks.length`，而 effect 内部调 `loadDisks()` 把它 0→1 ——
   effect 自我触发重跑，接口在 19ms 内被连发两次（生产构建实测，与 StrictMode 无关）。
   需要读瞬时值时用 `useStore.getState()`，不要放进依赖数组。

7. **SVG 图表不要无条件拉伸**
   `svg { width: 100% }` 会把自然尺寸很窄的图（如类图 153px 宽、却 369px 高）横向压扁；
   极宽的图又会被缩到字不可读。正确策略：`width:auto` + `max-width:100%` + `height:auto`
   （只等比缩小、不放大），容器给 `overflow-x:auto`。

8. **dev server 只监听 IPv6**
   Vite 在本机只绑 `::1`，自动化脚本一律用 `http://localhost:3000`，**不要用 `127.0.0.1`**（会连不上）。

9. **全局 `*` + `!important` 不得覆盖第三方渲染库依赖的属性**
   反面案例：为无障碍写的
   `@media (prefers-reduced-motion: reduce) { * { transition-duration:.01ms!important } }`
   **改变了 mermaid 的几何** —— 当该偏好生效时（Windows「关闭动画效果」、无头浏览器默认值都会命中），
   同一份笔记渲染成：类图外框只按标题高度画、成员文字跑到框外；ER / 流程图 / 状态图文字被裁；
   导出的 SVG 与屏幕同源，于是"下载的图片也不全"。
   实测逐条对照：**只留 `transition-duration` 照样坏**，只留 `animation-duration` 或整条去掉即正常。
   - 正确写法：全局冻结只作用于 HTML UI，**SVG 子树豁免**（`:not(svg):not(svg *)`）；
   - 同类高危属性：`transition` / `animation` / `transform` / `will-change` / `contain`；
   - 依据与复现：`.agents/skills/lessons/LESSONS.md` L23、`jflove-web/tests/utils/mermaid-geometry.test.ts`
     （`describe('reduced-motion 与引擎几何…')` 一条条锁死"只留 transition / 只留 animation / 整条去掉"的三种取值）。

10. **媒体/系统偏好类结论必须显式枚举，不能吃环境默认值**
    无头浏览器**默认报告 `prefers-reduced-motion: reduce`** —— 上面那条 CSS 只在 reduce 下生效，
    于是"只在无头里测"会得到与真实用户相反的结论（会把"我们自己的 CSS 问题"误判成"引擎缺陷"，
    白做一整套替代方案）。
    - 自动化里用 CDP `Emulation.setEmulatedMedia` **显式**指定该维度的每个取值，各测一遍；
    - 同页对比多种设置时**每次开新页面**（渲染结果有内存缓存，否则第二次读的是缓存）；
    - 下"这是库的缺陷"这种重结论前，先问：**还有哪个环境维度没被枚举？**
    - 依据：`.agents/skills/lessons/LESSONS.md` L24。

11. **离屏容器里不要用 `inverse(getCTM())` 做坐标换算**
    把 SVG 放在 `left:-99999px` 的离屏容器里渲染时，Chromium 的 `getCTM()`
    **把元素在页面里的位置也算进去了** —— `inverse(parent.getCTM())` 算出的坐标偏移可达 30 万，
    viewBox 直接暴涨到 68 万（实测）。用"相对 SVG 自身 `getBoundingClientRect()` 的差值 + 缩放比"
    换算，页面偏移天然抵消；矩阵法要用 `inverse(svgCTM) × elementCTM`（两者偏移相乘相消），
    不要单用 `inverse(parentCTM)`。

12. **渲染后处理必须"先插 DOM 再量"**
    反面案例：尺寸归一化曾对着一个空容器 `querySelector('svg')`，永远返回 `null`，
    于是"重新测量"从未生效、五类图的文字一直被裁掉却没人发现（**死代码**）。
    任何"渲染后量一次再修正"的逻辑，都要断言**元素已在文档里**，并加一条
    "调用顺序"的静态断言锁死（见 `tests/utils/mermaid-geometry.test.ts`）。

13. **占位符 / 特殊字符的约定必须"默认不生效"，且不能挑正文里常见的字符**
    反面案例：`insertMarkdown` 把插入文本里**第一个 `|`** 当光标占位符并无条件吃掉，
    而 mermaid 的 ER 关系语法恰好是 `||--o{` —— 用户点「ER 图」插入后**必报语法错误**
    （`用户 |--o{ 会话`，少了一根竖线）。7 个模板里只有 ER 图含 `|`，所以只它中招。
    - **默认原样插入**，只有显式声明的调用方（工具栏按钮）才解析占位符；
    - 占位符字符不能选 Markdown / mermaid / 表格里随处可见的（`|`、`*`、`_`、`` ` ``）；
    - 这类逻辑要从组件里抽成纯函数，才可能被单测覆盖。见 `.agents/skills/lessons/LESSONS.md` L25。

14. **验证"某功能产出什么"必须走用户真实路径**
    把源文件里的常量拿去渲染**不等于**验证了插入结果 —— ER 图那个缺陷就是这么漏掉一轮的：
    模板常量完全正确，坏在插入逻辑。正确做法是**真的点按钮、读编辑区、看预览**，
    并加**负向对照**（把修复前行为注入回去，确认用例会失败且失败信息与用户报的现象一致）。
    桩后端 + 伪造登录态的配方按版本需要临时编写、跑完即弃。
    回归用例见 `jflove-web/tests/utils/markdown-insert.test.ts`；负向对照的留档见
    `文档记录/Web端开发记录/v1.5.0.md`（「同一脚本的**负向对照**」：把修复前行为注回去后精确复现用户截图里的
    `Parse error on line 2: erDiagram 用户 \|--o{ 会话`）。


## 文档更新范围

- 路径：`文档记录/Web端开发记录/<版本号>.md`；同步更新 `jflove-web/README.md`
- 必须包含：功能与改动点、页面/组件/服务方法、调用的后端接口、与上一版本的逻辑差异、设计取舍
- README.md 必须包含：启动方式、构建方式、Docker 部署方式、路由表

## 版本号管理（发布阻塞项）

- **唯一真相 = 仓库根 `version.json`**，完整规则见 `AGENTS.md §8.1`（清单以 `scripts/sync_version.py::POSITIONS` 为准）。
  **Web 端只管 web scope 的 2 处**：`package.json` `version`、`src/config/constants.ts` `APP_VERSION`（设置页「关于」显示）。
  **禁止手动改任何版本号字段**；`jflove-web/build.py` 不再硬编码版本号（它只读 `version.json`，
  并在构建前用 `sync_version.check_consistency("web")` 校验，不一致直接中止构建）。
- 模块 `jflove-web/build.py` 的参数只有 `--tag` / `--no-cache` / `--save`，**没有 `--version`**；
  发布走仓库根统一入口 `python build.py -m web`（见 `AGENTS.md §8.2`）。

## 构建与验收

### 阶段 0（浏览器实机验证，强制）

```bash
node scripts/verify/web_shot.mjs --out /tmp/web.png
node scripts/verify/web_shot.mjs --click-text '连接服务端' --out /tmp/web-login.png
node scripts/verify/web_shot.mjs --width 390 --height 844 --mobile --out /tmp/web-mobile.png
```

- 本机浏览器是 `microsoft-edge`（Chromium 内核）；**没有** Chromium / Firefox。
- 交互优先用内置 CDP；复杂流程再引 `playwright-core`（`channel: 'msedge'`，无需下载浏览器）。
- 判据：截图 + 服务端出现 `key-exchange 200`；**只截首屏不算验证**（L50）。
- 用户会话锁屏时浏览器窗口会被 KWin 标 HIDDEN，截图黑/白；无头 Edge 不受影响。

### 阶段 1（日常开发）

```bash
cd jflove-web
npm run dev        # Vite dev server，HMR 热重载
npm run lint       # ESLint 检查
npm run test       # Vitest 单元测试
```

### 阶段 2（生产构建）

```bash
npm run build      # Vite 生产构建 → dist/
npm run preview    # 本地预览生产构建
```

### 阶段 3（Docker 镜像）

```bash
docker build -t jflove-web:<版本号> .
docker run -p 8080:80 jflove-web:<版本号>
# 访问 http://localhost:8080 验证
```

**Dockerfile 结构**（多阶段）：
1. **Stage 1（build）**：`node:22-alpine`，`npm ci` → `npm run build`
2. **Stage 2（serve）**：`nginx:1.27-alpine`，复制 `dist/` + `nginx.conf`

### 验收标准

- [ ] `npm run lint` 零警告
- [ ] `npm run test` 全通过
- [ ] PC 端（1920×1080 / 1366×768）布局正常
- [ ] 移动端（375×667 iPhone SE / 414×896 iPhone 11）布局正常
- [ ] 加密信封往返：密钥交换 → 登录 → 业务请求加解密正确（含 HTTP 非安全上下文纯 JS 回退路径）
- [ ] 流式帧解析：文件下载可逐帧解密
- [ ] 视频/音频边下边播：MSE 主路径（`playWithMSE`）在 HTTP 与 HTTPS 下均可播放 + seek
- [ ] 设置页「关于」显示版本号与 package.json 一致（**唯一真相 = 仓库根 `version.json`**，`python scripts/sync_version.py` 同步）
- [ ] Docker 镜像可正常启动并访问；`dist/` 含 index.html / assets / `logo-*.png`（**无 `sw.js`**）

## 安全宪法

详见 `AGENTS.md §9`。你的角色约束见 §9.6 表格 `web-frontend` 行。Web 端特有要点：

- **加密原语必须三端兼容**：`@noble/ciphers` 的 ChaCha20-Poly1305 输出必须与 Python `cryptography`、Dart `pointycastle` 的输出逐字节一致（同一 nonce + key + plaintext → 同一 ciphertext）。
- **Web Crypto API 的 X25519**：`SubtleCrypto.generateKey({name: 'X25519'}, ...)` 生成的 CryptoKey 对象**不可序列化**，公钥通过 `SubtleCrypto.exportKey('raw', ...)` 导出 `Uint8Array`，私钥**用完即销毁**（不导出、不存储）。
- **session_key 仅存内存**（`session.ts` 的闭包变量），不进入 Zustand store、localStorage、sessionStorage。
- **JWT token**：不存 `Authorization` header，走加密 body 的 `token` 字段（见 `AGENTS.md` §9.3 第 9 条）。
- **禁止在任何 URL 参数中携带业务数据**（包括文件 ID、笔记 ID 等）。
- **生产构建必须确保 source map 不泄露源代码路径**。

### 引入新 API / 新功能时的安全清单（自查）

设计或实现一个新接口调用前，逐条勾选：

- [ ] 请求 body 是否走 `http-client.ts` 加密信封？
- [ ] 成功响应是否通过 `http-client.ts` 解密信封？
- [ ] 错误响应是否通过 `http-client.ts` 解密信封后再显示？
- [ ] 路径参数（如 `/files/:diskId`）是否做了归属/角色校验？
- [ ] 是否需要处理文件流？如是，必须使用 `stream-frame.ts` 逐帧解密，不能直接 `response.blob()` 或 `<a download>`
- [ ] 是否在日志/调试输出中明文记录了 token、session_key、文件内容？
- [ ] 是否新增长期密钥 / 静态盐 / 写死对称密钥？严禁。
