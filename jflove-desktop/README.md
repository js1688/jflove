# jflove-desktop

JFLove 桌面客户端：**原生外壳（PySide6）+ Web UI（QtWebEngine 渲染）** 的个人数字工作台桌面端，界面与 Web 端同一套，并额外提供托盘、本地解码的媒体预览、原生文件对话框、系统拖放上传、内置文本编辑器与云盘挂载。

> **当前版本：v1.8.2**（版本号**单一来源 = 仓库根 `version.json`**；`python scripts/sync_version.py` 同步全部位置 —— 桌面端有**三处**（**以 `scripts/sync_version.py::POSITIONS` 的 desktop scope 为准**）：`src/config/settings.py` 的 `APP_VERSION`、**`ui/src/config/constants.ts`** 的 `APP_VERSION`（界面上「登录页页脚 / 设置-关于」显示的值）与 **`ui/package.json`** 的 `version`。**不要手改任何一处版本号字段。**）

> **v1.8.2**：媒体播放浮层改为 `QWebEngineView` 的 native 子控件（随主窗移动/缩放/全屏/托盘恢复，
> Wayland 与 X11 均不再被主窗遮挡；`JFLOVE_MEDIA_OVERLAY=window` 可回退旧实现）；
> 管理员「系统设置 → 离线下载」新增解析 / 连接 / 读取三项超时；目录选择器截断文案改用服务端 `limit`。
>
> **v1.8.1**：修复「已完成的任务进度条不是 100%」（`taskPercent` 改为成功优先满格）；
> 磁盘管理新增「浏览…」服务器目录选择器（管理员只读接口 `GET /api/v1/virtual-disks/server-dirs`），
> 手输真实路径保留为兜底。
>
> **v1.8.0**：新增「离线下载」页（任务中心 + 新建任务 + 设置，服务端异步分析网页并抓取媒体到账号自己的磁盘目录）；
> 修复 issue #7（拖动主窗视频画面不跟随/黑屏：`QVideoWidget` 原生 surface 在几何变化后显式重绘）与
> issue #5（Linux FUSE 卸载后 `.fuse_hidden*` 残留：卸载后递归清理）。#3 并入 #7。
>
> **v1.5.1**：Web 端品牌标识与桌面端统一（猫猫 logo）；系统设置移除**已失效**的「允许重编码降级」开关（服务端自 v1.4.2 起已不读取该键）；
> 修复原生媒体浮层「关窗后残留」与「拖动主窗画面不跟随」；笔记页大纲改为**右侧可折叠竖栏**、打开笔记默认进入预览模式（有内容时）；
> 修复「200 响应是加密信封但解不开却被当明文成功」的静默降级；版本号同步补入界面侧常量（此前界面会显示旧版本号）。
>
> **v1.5.0**：界面由 Qt Widgets **整体迁移到 Web UI**（QtWebEngine 渲染 `ui/`），保留托盘、本地解码媒体预览、原生文件对话框与系统拖放上传；交付形态改为**安装包**（onedir + Inno Setup / RPM）。

---

## v1.6.0 / v1.6.1 / v1.6.2：内置文本编辑器（Scratch Editor，**纯本地**）

> **v1.6.2 可用性修复与能力扩展**：
> ① **修「工具箱点了没反应」**——关框委托原先挂在弹框容器的 `pointerup` **捕获**阶段，React 在
> `pointerup` 之后、`click` 之前同步提交，把被点的按钮卸载掉，Chromium 于是**不再派发**这次
> `click` ⇒ 白名单（`sql` / `font`）之外的 **10 项、约 29 个按钮全部失效**；现改挂 `click`
> **冒泡**（被点按钮的 `onClick` 先跑、随后关框）。真机判据：`poc/editor-probes/r22-real-gui-v162.py`
> 的 `M1`/`M2`（真 OS 鼠标点「工具箱」→ 点「排序」→ 正文真的变成 `a\nb`），机制探针
> `poc/editor-probes/r21-pointerup-close-semantics.py`。
> ② **动作结果可见**：改正文类动作后把**首个差异位置**滚进视口 + 定位光标，并给中文结果提示
> （如「已排序（3 行）」「已 Base64 编码（4 字符）」）；无变化时提示「无变化」且**不写回**。
> ③ **两段文本对比真的能用了**——覆盖层原先被工具栏自己的 `relative` 抢走定位祖先（只有一行高），
> 现把定位祖先归还 `EditorPanel` 根 ⇒ 铺满编辑面板（真机 `M3` 判据：高度 ≥ 面板 90%）。
> ④ **工具箱 12 项各有快捷键**（11 条新增 `toolbox.*` + 对比沿用 `view.diff`），作用域 =
> **编辑面板聚焦**（焦点不在面板内 ⇒ 不响应、也**不吞键**）。
> ⑤ **查找 / 替换新增「跨标签搜索」**：范围默认当前标签页，可切到「所有已打开的标签页」
> （只搜已挂载标签页并明示未挂载数量；结果按标签页分组、点一下跳过去并选中；替换走**就地二次确认**）。
> **零新增桥能力**（不读磁盘、不新增桥方法）。
> ⑥ **右击菜单**：**只有可编辑上下文**（编辑器正文 / 输入框）给**中文**菜单（撤销 / 重做 / 剪切 /
> 复制 / 粘贴 / 全选，可用性跟随 Chromium 的 `editFlags`），页面其它区域**一律不弹**
> （Chromium 那份带 `Back` / `Reload` / `Inspect` 的英文菜单不再出现）；菜单动作经
> `QWebEnginePage.triggerAction()` 转发给 Chromium 自己执行 ⇒ 渲染层**不需要**剪贴板权限。
> ⑦ **快捷键配置分两组**：「应用级（任意页面生效）」/「文本编辑器级（仅编辑面板聚焦时生效）」，
> 每行显示作用域；**组内同键拒绝保存**，跨组同键只给提示（作用域不同，不会同时触发）。

> **v1.6.1 形态变更**：编辑器由「应用内**浮动窗格**」改为「**左侧导航里的整页工作区** + **顶部固定标签条**」——
> 一个文档一个标签页、切换标签 = 切换编辑面板（非激活面板 `hidden` 但**保持挂载**，切回不丢内容 / 光标 / 滚动位置）；
> 浮动窗格的拖动 / 8 向缩放 / 最小化 / 最大化 / 平铺 / 层叠 / z 序 / 几何**整体退役**（配置界面不再出现「平铺 / 层叠」）。
> 同时编辑器**只在登录后可用**（准入判据单一真相 `ui/src/utils/scratch/editor-gate.ts`）。
> **与远端零交互**（不读/不写虚拟磁盘与笔记）—— 内容只落本机草稿库或用户指定的本地文件。

| 能力 | 说明 |
|------|------|
| 登录准入 | **未登录零存在、零响应**：导航项不渲染、快捷键不响应（**不吞键**）、`/editor` 路由被守卫送回登录页；外部唤起（「打开方式」/ 命令行 / 单实例转交）先在**内存队列**里挂起（同路径去重、不持久化），登录成功后自动切到「文本编辑器」并把原文件**打开为激活标签页**。会话失效 / 登出后入口消失、工作区不再渲染，但**本机草稿不清理**（重新登录仍可还原） |
| 工作区与标签条 | 整页工作区（`data-scratch-workspace`）+ 顶部单行标签条：切换 / 关闭 / **指针拖拽排序**（阈值 4 px、中点判定、`Escape` 取消）/ 溢出时**横向滚动 + 溢出下拉**（12 个标签也能定位到最后一个）/ 未保存脏点；关闭最后一个标签页后留在**空态**（含「新建标签页」「打开本地文件」入口），不会被踢到别的菜单 |
| 内核 | **CodeMirror 6**，以**单文件 IIFE 离线分包**在运行期从 `ui/public/vendor/editor.bundle.js` 懒加载（标签页首次成为激活项才加载；`npm` 侧**不**引 `@codemirror/*` 依赖） |
| 块选择 | 修饰键（默认 `Alt`）+ 拖拽 = **矩形块选择**；复制口径为 IDE 行为（逐行取列区间、短行右侧补空格到块宽、`\n` 连接） |
| 差异化体验 | 按类型提供 格式化 / 压缩 / JSON 树 / Markdown 预览与分屏 / CSV 表格视图 / 行级 diff / 折叠 / 括号匹配；SQL 美化可配（方言 / 关键字大小写 / 缩进） |
| 工具箱 | 12 项：查找替换（含正则）+ 跳转行 + **跨标签搜索 / 替换**、行操作（排序/去重/去空行/去首尾空格）、大小写转换、JSON·HTML·URL 转义、Base64、JSON 树 + 格式化/压缩、Markdown 预览/分屏、CSV 表格、SQL 美化、**XML 格式化/压缩（自研，保注释与 CDATA）**、两段文本对比、字号与行号/换行开关。**每一项都有快捷键**（见下表 19~29 行；对比项沿用 14 行），**只在编辑面板聚焦时生效**；动作执行后给中文结果提示并定位变化处 |
| 右击菜单 | **中文**（撤销 / 重做 / 剪切 / 复制 / 粘贴 / 全选）；**只在可编辑上下文弹**（编辑器正文、应用内输入框），其它区域**不弹任何菜单**；由外壳 `DropAwareWebView.contextMenuEvent` + 纯函数 `context_menu_spec()` 生成（`src/ui/web_shell.py`），动作走 `QWebEngineAction` ⇒ 不开渲染层剪贴板权限 |
| 类型校验先行 | 扩展名白名单 → `stat` 大小阈值（**> 50 MB 在读内容前拒绝**；> 5 MB 降级只读但内容完整）→ 一次有界读 64 KB → 前 8 KB 嗅探（BOM / NUL / **严格解码优先**）→ 编码判定（utf-8 / utf-8-bom / gbk / utf-16le / utf-16be） |
| 保存语义 | 新建首次保存**必须**选路径；之后 `Ctrl+S` 直接更新该文件；**自动保存 1500 ms** 仅对**有保存路径**的文档生效；从未保存过的文档**永不**被静默写入磁盘（只进草稿库） |
| 草稿与还原 | 草稿库 `USER_DATA_DIR/storage/scratch/`（`index.json` + `pane-<id>.txt`，各自原子写；索引自 v1.6.1 起多白名单字段 `view_mode` / `order` / `active`，旧索引仍可读）；**崩溃/被杀后下次启动、且登录成功时自动还原为标签页**（按 `order` 排序、`active` 决定激活项；懒挂载，几十份也不拖慢启动）；正常退出弹「保存 / 放弃 / 取消 + 是否保留下次恢复」 |
| 快捷键 | **29 项**默认键位（18 项基础 + **11 项工具箱**，第 12 项对比沿用基础项「两段文本对比」），全部可配置（设置页「文本编辑器」+ 配置对话框，含**中文功能说明**、**按作用域分两组**、冲突检测、一键恢复默认）；**不注册任何全局热键**（只在窗口激活时生效）。快捷键运行时**模块级常驻**（`ui/src/utils/scratch/shortcut-runtime.ts`，挂在 `App.tsx` 静态 import 上），首行判准入 ⇒ 未登录时按键原样交给宿主；工具箱 11 项另要求**编辑面板聚焦**（面板外不接管、不吞键） |
| 文件关联 | 安装包注册到系统「打开方式」候选（Windows 写 **HKCU**；Linux 装 `application/x-jflove-text` MIME）；**不抢占默认程序**；应用内勾选只声明 JFLove 自己支持的范围（勾选状态落 `USER_DATA_DIR/storage/file_assoc.json`） |
| 单实例 | 第二实例**不新开窗口/托盘**，把文件路径转交给已运行实例后退出（`QLocalServer` 行分隔 JSON 协议 + `ack` 确认；转交失败降级为本地启动，绝不静默丢弃） |
| 安全边界 | 渲染层**无网络出口**、编辑器的远端调用 **0 个**；文件字节与令牌不进渲染层；日志不记文档内容与完整路径（`AGENTS.md §9`） |

### 文本编辑器默认快捷键（29 项：18 项基础 + 11 项工具箱）

> **设置页按作用域分两组**（v1.6.2）：**应用级**（`scope: 'window'`，任意页面生效）/ **文本编辑器级**
> （`scope: 'pane'`，仅编辑面板聚焦时生效）。**组内同键 ⇒ 拒绝保存**；**跨组同键 ⇒ 只给中文提示**
> （作用域不同，不会同时触发）。分组与作用域文案的唯一真相 = `ui/src/config/shortcuts.ts::SHORTCUT_SCOPE_GROUPS`。

| # | 功能 | 默认键 | 作用域 |
|---|------|--------|--------|
| 1 | 新建标签页 | `Ctrl+N` | 应用（任意路由页按键都会切到工作区） |
| 2 | 关闭当前标签页 | **`Ctrl+W`** | 编辑面板聚焦 |
| 3 | 下一个标签页 | `Ctrl+PageDown` | 应用 |
| 4 | 上一个标签页 | `Ctrl+PageUp` | 应用 |
| 5 | 打开本地文件 | `Ctrl+O` | 应用 |
| 6 | 保存 | `Ctrl+S` | 编辑面板聚焦 |
| 7 | 另存为 | `Ctrl+Shift+S` | 编辑面板聚焦 |
| 8 | 切换预览 / 分屏 | `Ctrl+Shift+V` | 编辑面板聚焦 |
| 9 | 查找 / 替换 | `Ctrl+F` | 编辑面板聚焦 |
| 10 | 跳转到行 | `Ctrl+G` | 编辑面板聚焦 |
| 11 | 切换行注释 | `Ctrl+/` | 编辑面板聚焦 |
| 12 | 块选择修饰键 | `Alt`（可选 `Ctrl`） | 编辑面板聚焦 |
| 13 | 打开工具箱 | `Ctrl+K` | 应用 |
| 14 | 两段文本对比（= 工具箱第 11 项的首选动作） | `Ctrl+Shift+D` | 应用 |
| 15 | 增大字号 | `Ctrl+=` | 编辑面板聚焦 |
| 16 | 减小字号 | `Ctrl+-` | 编辑面板聚焦 |
| 17 | 重置字号 | `Ctrl+0` | 编辑面板聚焦 |
| 18 | 切换自动换行 | `Alt+Z` | 编辑面板聚焦 |
| 19 | 工具箱 · 查找 / 替换面板 | `Ctrl+Shift+F` | 编辑面板聚焦 |
| 20 | 工具箱 · 行排序 | `Ctrl+Shift+P` | 编辑面板聚焦 |
| 21 | 工具箱 · 转大写 | `Ctrl+Shift+U` | 编辑面板聚焦 |
| 22 | 工具箱 · JSON 转义 | `Ctrl+Shift+E` | 编辑面板聚焦 |
| 23 | 工具箱 · Base64 编码 | `Ctrl+Shift+B` | 编辑面板聚焦 |
| 24 | 工具箱 · JSON 格式化 | `Ctrl+Shift+J` | 编辑面板聚焦 |
| 25 | 工具箱 · Markdown 预览 | `Ctrl+Shift+M` | 编辑面板聚焦 |
| 26 | 工具箱 · 表格视图 | `Ctrl+Shift+T` | 编辑面板聚焦 |
| 27 | 工具箱 · SQL 美化 | `Ctrl+Shift+Q` | 编辑面板聚焦 |
| 28 | 工具箱 · XML 格式化 | `Ctrl+Shift+X` | 编辑面板聚焦 |
| 29 | 工具箱 · 放大字号 | `Ctrl+Shift+O` | 编辑面板聚焦 |

> 19~29 的语义 =「该项的**首选动作**」，唯一真相表 = `ui/src/utils/scratch/toolbox.ts::TOOLBOX_PRIMARY_ACTION`
> （与弹框里的按钮**同一个动作函数**，不可能两处漂移）；一项里的**其它**动作仍走工具箱弹框。
> 真机判据：`poc/editor-probes/r22-real-gui-v162.py` 的 `K-F` ~ `K-O`（11 条逐条真按键生效）
> 与 `K-OUT`（焦点移出面板 ⇒ 不接管、不吞键、正文不变）。
> **新增/修改任何快捷键前必须跑** `poc/editor-probes/r23-keymap-collision.py`（退出码 0 = 与 CM6
> 已装配的 8 张 keymap 全部 0 命中）。`toolbox.lines` 的默认键就因此从 `Ctrl+Shift+L` 改成了
> `Ctrl+Shift+P`：`Mod-Shift-l` 是 CM6 `searchKeymap` 的「选中所有相同项」，配成它会**静默抢掉**
> 编辑器那个功能（与 v1.6.1 把 `Ctrl+D` 让回 CM6 同一条纪律）。

> **已作废（v1.6.1）**：`pane.tile`（平铺 `Ctrl+Alt+T`）、`pane.cascade`（层叠 `Ctrl+Alt+C`）—— 容器已无几何与层级。
> 「功能」列的真相 = `ui/src/config/shortcuts.ts::SHORTCUT_DEFS[].label`（本表按代码原文逐字对齐）。
> `Ctrl+W` 是否被宿主动作占用有**真机判据**：`poc/editor-probes/r13-ctrlw-takeover.py`
> （渲染层确实收到 / 宿主零默认动作 / 干净标签被关闭；结论见 `文档记录/桌面端开发记录/v1.6.1.md` §4）。
> **已闭环（v1.6.1）**：`view.toolbox`（`Ctrl+K`）与 `view.diff`（`Ctrl+Shift+D`）**现在都有接管方**
> （`ui/src/utils/scratch/shortcut-runtime.ts` 接管面 7→9 + `ui/src/stores/scratch-ui-store.ts` 的一次性 `panelRequest`）⇒ 改键位会真实生效（原 BL-51 已闭环）。
> **已闭环（v1.6.2）**：接管面 9 → **20**（新增 11 条工具箱键）；`shortcuts.json` 仍为 `version: 1`
> —— 旧文件里**没有**这 11 条 ⇒ 一律取默认值（**不需要清配置**）。

> **工具箱可用性的历史缺陷（v1.6.2 修复，写在防复发）**：v1.6.0 / v1.6.1 的「选完即关」挂在
> `onPointerUpCapture` 上，真实浏览器里 `pointerup` 与 `click` 是两个 task ⇒ 关框先卸载按钮、
> 这次 `click` 被整个吞掉 ⇒ 除 `sql` / `font` 外的 **10 项全部"点了没反应"**。
> **两类判据都必须留在测试里**：① 工具箱点击类用例**必须**把 `pointerdown` / `pointerup` / `click`
> 放进**三个独立 `act` 边界**（`ui/src/components/scratch/__tests__/scratch-toolbox-actions-v162.test.tsx`；
> 塞进同一个 `act()` 的写法**测不出**它，会假通过）；② 真机判据 `r22` 的 `M1`/`M2`。

> 单一真相：类型/扩展名表只写在 [`ui/src/config/scratch-types.ts`](jflove-desktop/ui/src/config/scratch-types.ts)，
> `ui/public/file-assoc.manifest.json` 与三处打包资产（`packaging/linux/jflove.xml`、
> `.desktop` 的 `MimeType=` 行、`packaging/windows/file-assoc.iss`）**全部由脚本派生**，
> 一致性判据：`python jflove-desktop/scripts/gen_file_assoc_assets.py --check`（退出码 0 = 一致）。

> 构建注记：CM6 离线分包**不入库**，由 `build.py::ensure_editor_bundle()` 在缺失时**就地生成**
> （`scripts/editor-bundle` 装依赖 + 跑 `scripts/build-codemirror-bundle.mjs`），生成失败才响亮失败
> —— 这样无需改 CI 也能保证打包产物不缺内核。

---

## 技术架构

### 框架与组件

| 组件 | 用途 |
|------|------|
| **PySide6 6.11.0** | Qt 绑定；原生外壳（窗口 / 托盘 / 媒体浮层）与 QtWebEngine 宿主 |
| **QtWebEngine** | 渲染前端工程 `ui/`（React + TS + Vite + Tailwind） |
| **Markdown 预览** | 渲染层 React 组件 [`ui/src/components/markdown/MarkdownRenderer.tsx`](jflove-desktop/ui/src/components/markdown/MarkdownRenderer.tsx)（marked + DOMPurify + highlight.js + Mermaid）；Qt 侧旧的 `src/components/markdown_view.py` 已随 Qt Widgets 界面删除 |

### 架构模式

采用 **Redux 模式**（基于信号槽实现）：

```
UI (Page) → Signal → Service (业务逻辑) → http_client → 后端 API
    ↑                      ↓
    └── Signal ←─── Response ────┘
```

| 层 | 目录 | 职责 |
|----|------|------|
| **UI 前端工程** | [`ui/`](jflove-desktop/ui/) | 全部业务界面（React + TS + Vite + Tailwind），与 Web 端同源快照、独立演进 |
| **原生外壳** | [`src/ui/`](jflove-desktop/src/ui/) | `web_shell.py`（无边框窗口 / 桥注入 / 关闭=最小化到托盘）、`tray.py`、`media_overlay.py`（原生媒体浮层，默认 `QWebEngineView` native 子控件） |
| **渲染层桥** | [`src/bridge/`](jflove-desktop/src/bridge/) | 白名单桥（`bridge.py` + `api_gateway.py` / `files.py` / `media.py` / `sync.py` / `transfer.py`）；JS **无网络出口** |
| **组件（原生能力）** | [`src/components/`](jflove-desktop/src/components/) | `stream_proxy.py`（本地解密 + Range 206 流代理，媒体预览的唯一通路） |
| **服务** | [`src/services/`](jflove-desktop/src/services/) | 与后端 API 交互的业务服务层 |
| **工具** | [`src/utils/`](jflove-desktop/src/utils/) | 加密、HTTP 客户端、会话管理、工作线程 |

### 原生媒体浮层（QWebEngineView 子控件）

媒体预览的画面由 **OS 解码器**渲染（`QMediaPlayer` / Qt 图像解码），渲染层只画播放器外壳并上报
预留矩形；它**不是独立顶层窗口**，而是 `QWebEngineView` 的 **native 子控件**：

```
QMainWindow
└── QWebEngineView（页面）
    └── MediaOverlay.window = QWidget(view)   ← WA_NativeWindow，坐标相对父控件
        ├── QVideoWidget
        └── QLabel（图片）
```

- 坐标：渲染层 `getBoundingClientRect()` 上报的就是**相对视口**的 CSS 像素，
  子控件直接 `setGeometry(rect)`（CSS 像素 == Qt 逻辑像素，**无 dpr 换算**）；
- 跟随：主窗移动/缩放/全屏/托盘恢复都由 Qt 父子关系自动完成，不再 `mapToGlobal` 反算；
- 全屏：`setGeometry(0, 0, view.width(), view.height())`（子控件不调用 `showFullScreen()`）；
- 层级：`show()` / 全屏切换 / 视口尺寸变化后 `raise_()`（`web_shell` 把 `view` 的 resize
  转发给同一条合帧重贴合链路）；
- **回退开关**：`JFLOVE_MEDIA_OVERLAY=window` 恢复 v1.8.1 的「独立无边框顶层窗口 + 全局坐标」
  实现（排障用）；`get_view()` 尚不可用时自动回退并记 warning；
- 几何取证脚本：[`scripts/verify/media_overlay_probe.py`](scripts/verify/media_overlay_probe.py)
  （X11 判像素 + 全局跟随；Wayland 用结构性判据，`Xvfb`/`kwin_wayland` 用法见脚本 docstring）。

> 安全口径不变：媒体仍由 Python 侧 `StreamProxy` 解密后交给 OS 解码器，渲染层不接触媒体字节/密钥。

### 通信层

```
Service Layer → http_client.py → 加密请求 → 后端 API
                    ↑
              crypto.py (X25519 + ChaCha20-Poly1305)
```

- 所有 HTTP 调用必须通过 [`src/utils/http_client.py`](jflove-desktop/src/utils/http_client.py)，禁止在 services/ui 层直接 `import requests`
- 自动处理：密钥交换 → 会话维护 → 请求加密 → 响应解密 → 错误响应解密（`_decrypt_envelope_or_none`）
- 服务端重启后自动重新交换密钥并透明重发请求（用户无感知）

### 运行环境

- **语言**：Python 3.14+
- **UI 框架**：PySide6 6.11.0
- **界面**：Web UI（`ui/`，与 Web 端同源快照、独立演进）
- **HTTP**：requests
- **加密**：cryptography（X25519 / HKDF-SHA256 / ChaCha20-Poly1305）
- **构建**：PyInstaller（**onedir**；交付形态为**安装包**，见「构建与打包」）

> **Python 版本口径（BACKLOG BL-5 留痕，2026-10-14 实测）**
>
> - **要求侧**：Python **3.14+** —— 本文件上方「语言」、`AGENTS.md §2`、根 `README.md` badge、
>   `jflove-server/Dockerfile:5`（`FROM python:3.14-slim`）、CI workflow 均为 3.14。
> - **本地实测侧**：`venv-win/` 实际为 **3.13.14**（本机 `Programs\Python` 下**只有 `Python313`**，
>   无 3.14、无 `py` launcher）。**v1.6.0 的全部用例在该版本下全绿**：
>   `flake8 src/ tests/ --max-line-length=99` → 0 警告；`pytest tests/` → **133 passed**（v1.5.1 基线）。
> - **本次决定（D14）：不升级本地环境**。差异的性质是「**本地环境低于项目要求**」，
>   **不是文档写错**；CI 与镜像继续使用 3.14。
> - 「把本地 venv 升到 3.14」已留在 `文档记录/项目管理记录/BACKLOG.md`，不在 v1.6.0 内做。

---

## 环境要求与依赖安装

> **多平台共存**：项目源码在 Linux / Windows / macOS 之间通过同步盘共享时，**必须为每个平台单独建一个 venv**（虚拟环境内含 absolute path、平台二进制，跨平台无法复用）。本项目约定按平台后缀命名：

| 平台 | 虚拟环境目录 |
|------|------------|
| Linux | `venv-linux/` |
| Windows | `venv-win/` |
| macOS | `venv-mac/` |

### Linux

```bash
cd jflove-desktop
python3 -m venv venv-linux
source venv-linux/bin/activate
pip install -r requirements.txt
```

> **云盘挂载（FUSE）额外要求**：Linux 的系统级挂载由 `fusepy` + 系统的 FUSE 2 运行库实现。
> 需要 `fuse`（提供 `/usr/lib*/libfuse.so.2` 与 `fusermount`）和可访问的 `/dev/fuse`：
>
> ```bash
> sudo dnf install fuse fuse-libs          # Fedora
> # Debian/Ubuntu: sudo apt install fuse libfuse2
> ```
>
> 缺任一条件时 `mount_vfs.is_available()` 返回 False，自动同步照常运行（只是没有系统级挂载）。
> 进程被强杀后可能残留「传输端点尚未连接」的死挂载点：应用启动时会自动清理并重新挂载
> （检测走 `/proc/self/mounts`，不依赖 stat —— 死挂载点 stat 会 ENOTCONN）。

> **云盘挂载 = 自动双向一致**：本地新增 / 修改自动上传，远端新增 / 修改自动落到本地，
> 读取「在线」文件时按需下载。**本地删除只删本地缓存，不会删除远端文件**
> （远端删除请到文件管理页操作）。因此界面上没有「下载 / 释放」按钮 —— 一切自动完成。
>
> 页面上可直接看到同步进度：汇总行（已同步 / 待上传 / 上传中 / 下载中 / 失败 / 在线），
> 以及「正在同步 N 项」面板逐条列出**文件名 + 上传/下载方向 + 百分比**。
> 每 15 分钟还会自动做一次全量核对，清理远端已被绕过 API 删除的残留条目。
>
> **大文件上传**：断点续传（`storage/upload_resume.json` 记录已发送分片，应用重启后继续），
> 分片级重试（1MB 分片失败只重发该片），服务端合并最长可等 1 小时；合并超时会核验远端文件
> 是否其实已落盘，**不会整文件重传**。单文件失败后 30 秒自动重试。
>
> **上传期间远端目录为什么看不到文件**：分片先写到服务端的 `build/upload_tmp/<upload_id>/`，
> 传完后服务端合并、校验，最后原子替换进磁盘目录。界面上的「校验中 / 上传中 / 服务端合并校验中」
> 分别对应「算哈希 / 传分片 / 合并」，三者都到 100% 且变成「已同步」才算真正落盘。
> 合并阶段会显示**服务端真实合并进度**（`服务端合并校验中 N% · 已 X 秒`），
> 该百分比来自 `POST /api/v1/files/upload/progress`；服务端未升级时降级为只显示「已 X 秒」。

> **Windows 与 Linux 的差异**：Windows 走 CfAPI（无写回调，**10 秒轮询**发现本地改动），
> Linux 走 FUSE（写回调即时同步 + 30 秒兜底扫描）；两者的状态判定、删除语义完全一致。

> **中文输入（Linux）**：Wayland 会话且检测到 Wayland 输入法前端（fcitx5 / ibus）时使用
> `wayland` 平台 + `QT_IM_MODULE=wayland`（xcb 下 PySide6 没有可加载的 fcitx 插件）。
> 若遇到 Wayland 显示异常，可用 `JFLOVE_QPA_PLATFORM=xcb` 启动回退（回退后输入法依赖 ibus）。

### Windows (PowerShell)

```powershell
cd jflove-desktop
python -m venv venv-win
.\venv-win\Scripts\Activate.ps1
pip install -r requirements.txt
```

### macOS

```bash
cd jflove-desktop
python3 -m venv venv-mac
source venv-mac/bin/activate
pip install -r requirements.txt
```

> **同步盘提醒**：使用云盘 / Samba 同步代码时，请把 `venv-*/` 目录加入同步软件的排除列表，否则跨平台同步来的 venv 会因 absolute path 与平台二进制不兼容而失效。

---

## 运行

确保 `jflove-server` 已启动并可访问：

```bash
cd jflove-desktop
python src/main.py
```

### 首次引导流程

1. 输入服务端地址（默认 `http://localhost:8989`）
2. 若系统尚无管理员，创建管理员账号
3. 登录进入主界面

---

## 构建与打包

> **统一入口在仓库根**（`AGENTS.md §8`）：本地打包一律走根目录 `python build.py -m desktop`（自动同步版本号 + 逐模块环境检查 + 切换到本模块 venv）。
> 直接调用本模块 `build.py` 只用于单步调试，**不作为交付路径**。

```bash
# 仓库根目录执行（唯一对外入口）
python build.py -m desktop      # Windows：onedir + 安装包 + 便携 zip；Linux：onedir + RPM
python build.py -m all          # 四端统一入口（缺 docker 时 server/web 会响亮跳过）
```

### 构建产物（桌面端交付形态 = **安装包**）

| 平台 | 产物 | 说明 |
|------|------|------|
| Windows | `build/dist/JFLove-<ver>-win64-setup.exe`、`JFLove-<ver>-win64-portable.zip`、`build/dist/JFLove/` | Inno Setup 安装包（含开始菜单/卸载器）；缺 Inno Setup 时**降级只出 zip 并响亮报警**（需 Windows 主机构建） |
| Linux / Fedora | `jflove-desktop-<ver>-1.fc<N>.x86_64.rpm`、`build/dist/JFLove/` | 由 `packaging/linux/build_rpm.sh` / CI 产出 |
| 其他 | `build/dist/JFLove/` | 只出 onedir（PyInstaller 不能交叉编译） |

> `--mode onefile`（单体 exe）仅为**兼容保留的显式选项**，**禁止**作为默认或交付形态：onefile 每次启动要解压数百 MB（实测「启动→首窗可见」中位 9.58s → onedir 1.31s，快约 7.3×），且 onedir 才配得上正常安装模式。

---

## 项目结构

```
jflove-desktop/
├── src/
│   ├── main.py                     # 应用入口（构造 WebShell + 原生浮层 + 桥装配）
│   ├── bridge/                     # JS↔Python 白名单桥（渲染层无网络出口）
│   │   ├── bridge.py               # 分发入口 + 向渲染层推事件
│   │   ├── api_gateway.py          # api.request 路径白名单（拒绝会话管理类路径）
│   │   ├── files.py                # 原生文件对话框 / 拖放 / 下载保存
│   │   ├── media.py                # 媒体打开/几何上报/播放控制的桥接
│   │   ├── sync.py                 # 旧同步引擎信号 → 桥事件（过渡期兼容）
│   │   ├── mount.py                # 云盘挂载：挂载点 / 增量 / 按需下载 / 释放
│   │   └── transfer.py             # 传输任务信号 → 桥事件
│   ├── components/
│   │   └── stream_proxy.py         # 本地解密 + Range 206 流代理（媒体预览唯一通路）
│   ├── config/settings.py          # 全局配置（APP_VERSION 由 version.json 同步）
│   ├── services/                   # 业务服务层（唯一 HTTP 出口在 utils/http_client.py）
│   │   ├── auth_service.py / file_service.py / note_service.py / user_service.py
│   │   ├── disk_service.py / permission_service.py / repair_service.py
│   │   ├── mount_service.py        # 挂载点配置（本地 JSON）+ 旧同步配置迁移
│   │   ├── mount_sync_service.py   # 增量拉取 / 全量对账 / 下载 / 释放 / 状态
│   │   └── config_service.py / sync_service.py / server_history_service.py
│   ├── ui/                         # 原生外壳（无边框窗口 / 托盘 / 媒体浮层）
│   │   ├── web_shell.py            # 承载 QtWebEngine、注入桥、关闭=最小化到托盘
│   │   ├── tray.py                 # 系统托盘（显示窗口 / 主题 / 退出）
│   │   └── media_overlay.py        # 原生媒体浮层（native 子控件贴预留矩形，OS 解码器渲染）
│   └── utils/
│       ├── crypto.py               # X25519 + HKDF + ChaCha20-Poly1305
│       ├── http_client.py          # 加密 HTTP 客户端（含信封形状判定）
│       ├── session.py              # 会话状态单例
│       ├── sync_engine.py          # 旧同步引擎（定时器 + 工作线程，过渡期保留）
│       ├── mount_index.py          # 挂载点本地索引（SQLite：文件清单 + rev 游标）
│       ├── mount_vfs.py            # 系统级挂载门面（按平台选实现 + is_available）
│       ├── mount_cfapi.py          # Windows 实现：Cloud Filter API 占位 + 水合
│       ├── mount_fuse.py           # Linux 实现：FUSE 视图 + 读取时按需下载
│       ├── transfer_manager.py     # 传输任务管理器
│       ├── worker.py               # QThread 异步工作线程
│       └── icon.py / input_method.py / logger.py
├── ui/                             # 前端工程（React + TS + Vite + Tailwind，与 Web 端同源快照）
│   ├── src/                        # pages / components / layouts / stores / services / styles / hooks / types
│   ├── public/                     # logo-64/128/256.png 等静态资源
│   └── dist/                       # 前端产物（构建生成；打包时作为 webui/ 进包）
├── packaging/                      # 打包资产（Windows Inno Setup / Linux RPM / spec / 桌面项）
├── tests/                          # pytest（含浮层生命周期/子控件几何与信封用例）
├── poc/                            # 一次性验证脚本（非交付物）
├── images/                         # 应用图标 icon.ico / icon.png
├── build.py                        # 模块构建脚本（onedir + 安装包；交付走仓库根 build.py）
├── requirements.txt                # 运行时 + 打包依赖（pytest / flake8 为开发依赖，不入此文件）
└── README.md                       # 本文件
```

---

## 加密协议

所有 API 请求（除明文白名单外）均采用端到端加密：

| 环节 | 算法 |
|------|------|
| 密钥交换 | X25519 ECDH + HKDF-SHA256（盐 `b"jflove-v1"`，32B 派生密钥） |
| 数据加密 | ChaCha20-Poly1305（12 字节随机 nonce） |
| 身份认证 | JWT（ES256，通过加密 Body 传递） |
| 文件流加密 | 64 KB 分片独立加密帧 `[4B 长度][12B nonce][密文+16B tag]` |

会话密钥仅保存在内存中，程序重启后需重新进行密钥交换。

---

## 用户数据目录

v1.1.5+，所有持久化数据统一保存在用户数据目录：

| 平台 | 路径 |
|------|------|
| Windows | `%APPDATA%/JFLove/` → `C:\Users\<用户名>\AppData\Roaming\JFLove\` |
| Linux | `~/.local/share/JFLove/`（或 `$XDG_DATA_HOME/JFLove`） |
| macOS | `~/Library/Application Support/JFLove/` |

### 目录结构

```
JFLove/
├── storage/
│   ├── session.json            # 会话信息（token、server_url、username 等）
│   ├── server_history.json     # 服务端地址历史（最近 10 条）
│   ├── sync_configs.json       # 旧同步配置（过渡期保留，首次挂载时自动迁移）
│   ├── mounts.json             # 挂载点配置（按账号隔离：mounts_{username}.json）
│   └── mount_index.db          # 挂载点本地索引（SQLite）
└── logs/
    ├── app.log                 # INFO 及以上级别日志
    └── error.log               # ERROR 级别日志
```

> **安全说明**：`sync_configs.json` / `mounts_*.json` 仅存配置元数据（不存 token / session_key / 密码）；`mount_index.db` 只存远端文件路径/大小/mtime，不存文件内容。`session.json` 不存储 session_key 或密码。

---

## 依赖清单

核心依赖（详见 [`requirements.txt`](jflove-desktop/requirements.txt)，仅运行时 + 打包依赖；`pytest`/`flake8` 为开发依赖，不入 requirements.txt）：

| 依赖 | 用途 |
|------|------|
| `PySide6==6.11.0`（含 QtWebEngine / QtMultimedia） | Qt 绑定；原生外壳 + 界面渲染引擎 + 本地解码播放 |
| `cryptography==47.0.0` | X25519 / HKDF-SHA256 / ChaCha20-Poly1305 加密 |
| `requests==2.33.1` | HTTP 客户端（仅 `src/utils/http_client.py` 使用） |
| `pyinstaller==6.20.0` | 应用打包 |
| `fusepy==3.0.1`（仅非 Windows） | Linux 系统级挂载驱动（FUSE 2；Windows 用系统自带 Cloud Filter API） |

> **更正（v1.5.1）**：本表此前还列了 `markdown` 与 `pygments` —— 那是 Qt Widgets 版 Markdown 预览的遗留依赖，
> 界面迁移到 Web UI 后已从 `requirements.txt` 移除。
> **v1.7.0**：新增 `fusepy`（带 `sys_platform != "win32"` 标记，Windows 侧不安装）。
