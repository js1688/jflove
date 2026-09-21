# P0-1 PoC：无边框窗口 + Web 自绘标题栏

> 对应计划：`plans/desktop-webui-migration-v1.5.0.md` §3 Phase 0 / P0-1
> 状态：**已验证通过（28/28 自动断言）**，等待人工验收拖拽与缩放手感

## 1. 这个 PoC 要回答什么

桌面端最终形态是「PySide6 外壳 + QtWebEngine 渲染 Web UI」。P0-1 只验证**外壳**这一层：

| 问题 | 结论 |
| --- | --- |
| 能不能去掉 Qt 原生外框？ | ✅ `FramelessWindowHint`，窗口外观 100% 由 Web 绘制 |
| 标题栏的 最小化/最大化/关闭 能不能在 Web 里画、在 Web 里点？ | ✅ 三个按钮由 HTML/CSS/SVG 绘制，点击经桥调 Python |
| 能不能拖拽移动？ | ✅ JS 判定按下 → 桥 → `QWindow.startSystemMove()` |
| 能不能 8 向缩放？ | ✅ JS 判定边缘热区 → 桥 → `QWindow.startSystemResize(edges)`，光标随边缘变化 |
| 双击标题栏能不能最大化/还原？ | ✅ 自实现双击判定（理由见 §5），最大化状态由 Python 推回 Web 切换图标 |

**为什么拖拽不能像 Electron 那样一行 CSS 搞定**：`-webkit-app-region: drag` 是 Electron 的私有扩展，
QtWebEngine（Chromium 通用内核）不支持。所以必须走「JS 判意图 → 桥 → Qt 系统调用」。

## 2. 文件构成

```
poc/frameless/
├── main.py                     # 无边框外壳 + 窗口控制桥（WindowBridge / ShellWindow）
├── web/
│   ├── index.html              # 标题栏 + 侧栏/内容占位
│   ├── app.css                 # 照抄 Web 端设计令牌（--header-h / --sidebar-w / 品牌色 …）
│   └── app.js                  # 边缘热区判定、拖拽、双击、最大化图标切换、桥初始化
├── test_headless.py            # 自动化自检（28 条断言，退出码即判据）
├── out/                        # 截图产物（shell_initial.png = 设计原貌）
└── README.md
```

## 3. 怎么跑

```powershell
# ① 人工体验（真机窗口，自己拖一拖、拉一拉）
jflove-desktop\venv-win\Scripts\python.exe poc\frameless\main.py

# ② 自动自检（离屏，无需显示器；startSystemMove 会返回 False，属预期）
jflove-desktop\venv-win\Scripts\python.exe poc\frameless\test_headless.py

# ③ 真机平台自检 + 真实截图（--skip-move：真机上调 startSystemMove 会进入系统模态等待，自动跑会卡住）
jflove-desktop\venv-win\Scripts\python.exe poc\frameless\test_headless.py --platform=windows --skip-move

# Linux（强制 XWayland）
jflove-desktop\venv-linux\bin\python poc/frameless/test_headless.py --platform=xcb
```

> Windows 控制台若出现中文乱码，先执行：
> `$env:PYTHONIOENCODING='utf-8'; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8`

## 4. 人工验收清单（自动断言覆盖不到手感）

1. 拖动标题栏空白处 → 窗口跟随移动，松手后停在该位置
2. 鼠标移到 4 条边 / 4 个角 → 光标变成对应缩放光标，拖动可改变窗口大小
3. 双击标题栏空白处 → 最大化；再双击 → 还原（右上角图标同步在“最大化/还原”间切换）
4. 点「最大化」按钮 → 最大化；再点 → 还原
5. 点「最小化」按钮 → 最小化到任务栏
6. 点「关闭」按钮 → 窗口关闭

## 5. 实测发现（都已在代码注释中固化）

### 5.1 `QWebEngineScript` 的 worldId 默认是 **ApplicationWorld(1)**，不是 MainWorld(0)

这是本 PoC 最耗时的一个坑：注入 `qwebchannel.js` 时若不显式
`script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)`，脚本会跑在隔离世界里，
页面主世界的 `app.js` **看不到 `window.QWebChannel`**。

症状具有误导性：`qt.webChannelTransport` 正常存在（说明 `setWebChannel()` 生效了），
但 `typeof QWebChannel === 'undefined'`，桥的所有方法都报 "method unavailable"。

> 结论：**N4（桥的正式形态）必须显式设置 worldId**，并保留现在这条诊断输出作为回归哨兵。

### 5.2 `QWidget.grab()` 抓 QWebEngineView 有「旧帧」问题

在真机上，**刚做过最小化→还原之后立刻截图，会抓到尚未重绘的旧帧**（表现为局部空白）。
本 PoC 初期就因此误判「标题栏按钮没画出来」——实际 DOM 几何、颜色、SVG 尺寸全部正常，
把截图时机提到最小化之前就正常了。

> 结论：截图只能当辅助证据，**判定 UI 是否正确要看 DOM 断言（几何 + 计算样式 + 图标尺寸）**。
> `test_headless.py` 里的「窗口按钮真实可见且图标有实际尺寸」这条断言就是为此加的。

### 5.3 其他

- `:/qtwebchannel/qwebchannel.js` 资源**必须 `import PySide6.QtWebChannel` 之后**才注册
  （否则 `QFile.exists()` 为 False）。
- `Qt.Edge` 的成员名是 `LeftEdge/TopEdge/RightEdge/BottomEdge`（没有 `Qt.Edge.Left` 这种别名）。
- 本机 `dpr = 1.25`（Windows 125% 缩放），Web 内容随之等比放大，尺寸令牌仍以 CSS px 为准。

## 6. 已知限制 / 明确不在本 PoC 范围

| 项 | 说明 |
| --- | --- |
| 窗口阴影 | 无边框后系统不再提供阴影；Windows 11 圆角也一并丢失 —— 是否补（DWM 圆角 + 自绘阴影边距）留到 N1 决定 |
| 本地资源协议 | 当前走 `file://`；N1 改为自定义 scheme + 严格 CSP |
| 关闭行为 | 当前是真关闭；正式形态为「最小化到托盘」（N17） |
| 标题栏交互细节 | 未做：右键系统菜单（Win11 贴靠布局 `WM_NCHITTEST` → `HTMAXBUTTON`）、多显示器 DPI 变化处理、窗口贴边半屏 |
| 桥的范围 | 只含窗口控制；业务桥（服务白名单分发、事件推送）是 P0-2 / N4 |
| 侧栏与内容区 | 纯视觉占位，Phase 1 由真实 Web UI 接管 |
