# A0 — 桥与外壳（主对话自查面）

> 面范围：`jflove-desktop/src/bridge/*`、`jflove-desktop/src/ui/web_shell.py`、`src/ui/tray.py`、
> `src/main.py` 的接线，以及"渲染层信任边界"这一横切安全面。
> 依据：`文档记录/设计文档/v1.6.0.md` §6.4 / §6.6 / §7.1 / §7.5 / §8 / §10；
> `文档记录/需求文档/v1.6.0.md` AC-42 / AC-46 / AC-47 / AC-56 / AC-57 / AC-58 / AC-R6；
> 工程纪律 `.agents/skills/code-review/SKILL.md`（8 条）。

---

## 发现

### [严重] 桥对**任何来源**的文档都暴露，且外壳没有任何导航策略

**位置**
- `jflove-desktop/src/ui/web_shell.py:426-432` — `self.view.page().setWebChannel(self.channel)`
- `jflove-desktop/src/ui/web_shell.py:540-556` — `_inject_qwebchannel_js()`：`DocumentCreation` + `MainWorld` 注入
  `:/qtwebchannel/qwebchannel.js`（**对该 page 的每个文档生效，不分来源**）
- `jflove-desktop/src/ui/web_shell.py:512-526` — `_harden_settings()` 只设两条属性
- 全仓 `jflove-desktop/src/` grep `acceptNavigationRequest|createWindow|newWindowRequested` → **0 命中**

**违反**
- 安全宪法 `AGENTS.md` §9.6 `cross-platform-desktop` 行：「渲染层（JS）**无网络出口**，一切与后端的通信
  只经 `src/bridge/bridge.py` 的白名单桥」「**文件字节与密钥严禁进入渲染层**」。
- 设计 §8.2 明确接受"文本进渲染层"这一边界让步，其**安全前提**是「渲染层只加载自家本地产物」。
  该前提在本版实现中**没有任何约束**：`LocalContentCanAccessRemoteUrls=False` 只管**子资源/fetch**，
  不管顶层导航，也不管"文档本身来自哪里"。

**证据**（可复现，Qt 6.11.0 + PySide6，offscreen）

```powershell
jflove-desktop/venv-win/Scripts/python.exe jflove-desktop/poc/editor-probes/r7-webengine-channel-origin.py --platform=offscreen
# 产物：jflove-desktop/poc/editor-probes/out/r7-offscreen.json / r7-offscreen.log
```

探针按 `web_shell.py:426-432,540-556` 的**原样接线**（`setWebChannel` + `DocumentCreation`/`MainWorld`
注入）复现外壳，桥上一个可调用对象 `probe.ping(str)`；被测页面自带一份 `qwebchannel.js`（远端页的真实做法）。

| 阶段 | 实测输出 | 含义 |
| --- | --- | --- |
| T1 对照 `file://` | `bridge_calls=['origin=file:// scheme=file: tag=T1-file']` | 观测链路有效（对照成立） |
| **T2 `http://127.0.0.1:<port>`** | `bridge_calls=['origin=http://127.0.0.1:56321 scheme=http: tag=T2-http']` | **非本地源文档成功调用了 Python 槽** |
| **T3 JS 顶层导航** | `file:///…/control.html -> http://127.0.0.1:56321/probe.html`，`nav=True` | **无导航策略**：`location.href` 即可把 view 交给远端页 |
| T4 `<a target="_blank">` 点击 | `new_window=False`、`same_view_navigated=False` | Qt 默认丢弃该请求（非本版改动，见"观察 4"） |

**影响**：本版桥含 `editor.readTextOnly` / `editor.detectAndRead` / `editor.writeText`
（`src/bridge/editor.py:283/305/333`）—— 渲染层据此获得**任意路径本地文件读写**。
一旦渲染层被任何导航路径交给远端页（T2+T3 已证明两端都成立），远端 JS 即可直接读写本机文件。

**建议**（三处小改，均在 `web_shell.py` 内）
1. 覆写 `QWebEnginePage.acceptNavigationRequest`：只放行自家产物目录（`resolve_index_html()` 所在目录）
   的 `file://` 与 `about:blank`，其余 `Accept`→`Reject`；
2. 覆写 `createWindow` 返回 `None`（把 T4 的"丢弃"从默认行为变成显式承诺）；
3. 需要"外链用系统浏览器打开"时走 `QDesktopServices.openUrl`（不要让 view 自己导航）。

> 定级理由：安全宪法 §9.6 的 code-review 行要求「发现违反一律标记**严重**」；且本版新增的
> 任意文件读写能力使该边界从"理论"变成"高危"。

---

### [严重] AC-46 ② / 设计 §7.5 ③ 的「先 `restore_from_tray()`，再打开」**未实现**

**位置（设计要求）**
- 设计 §6.6.3 表：`newConnection → … → shell.restore_from_tray() + notify_external_paths(paths)`，
  备注「**恢复可见优先于打开（AC-46 ②）**」（`文档记录/设计文档/v1.6.0.md:870`）
- 设计 §7.5 ③：「转发路径**也走 ③**：`restore_from_tray()` 先执行（窗口可见），再入队 → 推送」（`:1105`）
- 设计时序图：`收到转发路径 --> 常驻: restore_from_tray() + 事件 editor.openPaths`（`:854`）

**位置（实现）**
- `jflove-desktop/src/main.py:165` — `guard.set_paths_sink(lambda paths, source: _shell.notify_external_paths(paths, source))`
  （**只有** `notify_external_paths`，无 `restore_from_tray()`）
- `jflove-desktop/src/utils/single_instance.py:778-789` — `_deliver()` 只调 `self._sink(...)`
- `jflove-desktop/src/ui/web_shell.py:972-1032` — `notify_external_paths()` / `set_renderer_ready()` /
  `flush_pending_open_paths()` 全程**不动窗口可见性**
- `ShellControls`（`web_shell.py:218-290`）暴露的槽只有
  `isMaximized/minimize/toggleMaximize/close/reportTheme/startSystemMove/startSystemResize/platformInfo`
  —— 渲染层**没有**可以"把窗口带回前台"的接口
- 全仓 `src/` grep `restore_from_tray` → 只有定义 `web_shell.py:604` 与调用 `tray.py:76`（菜单）/`tray.py:143`（托盘单击）

**违反**：AC-46 ②「已有实例窗口**恢复为可见**」；设计 §6.6.3 / §7.5 ③ 明文步骤。

**证据**（命令）

```powershell
git grep -n "restore_from_tray" -- jflove-desktop/src
# src/ui/web_shell.py:604  def restore_from_tray(self) -> None:
# src/ui/tray.py:76        show_action.triggered.connect(self._window.restore_from_tray)
# src/ui/tray.py:143       self._window.restore_from_tray()
# （main.py / single_instance.py / notify_external_paths 链路中一次都没有）
```

**影响**：主实例处于**托盘 / 最小化**时，用户从文件管理器「打开方式 → JFLove」唤起——
窗格被建出来了，但**窗口仍不可见**，用户看不到任何反应（AC-46 ② 不可通过；AC-45 在"应用未运行"
的冷启动路径下仍成立，因为那时自己就是首实例，`main.py:167` 会 `_shell.show()`）。

**建议**：`main.py:165` 的 sink 改为先恢复可见再交付，例如

```python
def _deliver_paths(paths, source):
    _shell.restore_from_tray()      # 设计 §6.6.3：恢复可见优先于打开
    _shell.notify_external_paths(paths, source)
guard.set_paths_sink(_deliver_paths)
```

（放在 sink 里而不是 `notify_external_paths` 内，是因为 `notify_external_paths` 同时服务"命令行首实例"
路径——那条路径上 `main.py:167` 本来就会 `show()`，不必重复。）

---

### [中等] 退出标记落盘：私有原语耦合 + 异常全吞 → 潜在静默失败（**已知并接受，需落账**）

**位置**：`jflove-desktop/src/ui/web_shell.py:917-942`（`_save_exit_marks`，`except Exception` 只
`logger.error`）、`:944-968`（`_mark_abnormal_exit`，直接使用 `scratch_service._index_lock` /
`_ensure_dirs` / `_load_index` / `_write_index` 四个**私有**原语）。

**判断**：**不要求本版修**，理由与兜底确认如下（纪律⑤）
- 失败方向安全：写不进去只会"保留草稿 / 退出标记维持原值"，不会清空用户内容；
- 已自述并已留改进口：docstring 明写「⚠ 建议（已回报）：由草稿服务的负责人在 `scratch_service.py`
  补一个公开的 `mark_abnormal_exit()`」，且实现用 `getattr` **优先**调用该公开函数（`:956-959`）；
- 同锁 + 同原子写，不绕开 `scratch_service` 的不变式（不新增文件、不改他人模块）。

**证据**：`web_shell.py:928-942`（吞异常）、`:960-967`（四个私有原语）；
`文档记录/桌面端开发记录/v1.6.0.md` 已登记该点。

**建议**：Phase 7 落账为 BACKLOG 条目（草稿服务提供公开 `mark_abnormal_exit()`），否则该调用点会在
未来重构中**静默失效**（异常被 `logger.error` 吞掉，只在日志里可见）。

---

### [轻微] Markdown 预览里的外链点击**无任何反应**（需求未规定，属新增体验项）

**位置**：`jflove-desktop/ui/src/utils/markdown.ts:161-163`（外链输出 `href=… target="_blank"`）、
`ui/src/components/scratch/views/MarkdownSplitView.tsx`（预览即渲染 `renderMarkdown` 结果）。

**证据**：R7 探针 T4 —— `<a target="_blank">` 点击后 `new_window=False`、`same_view_navigated=False`；
全仓 `ui/src` grep `window.open|shell.open|openUrl` → 仅 `markdown.ts` 一处 `href`/`_blank` 命中，
渲染层没有"交给系统浏览器"的通道。

**违反**：无。需求文档 grep「外链 / 浏览器 / 链接」只有 1 处**无关**命中
（`文档记录/需求文档/v1.6.0.md:87`），本版**没有**"外链用系统浏览器打开"的 AC。

**建议**：登记为体验项（若后续要支持，按上面"严重 #1"的第 3 条实现，顺带把外链从 view 里摘出去）。

---

### [轻微] AC-R6 的字面判据「`jflove-app/` 零改动」与用户裁决 D-A **冲突**（已授权例外，建议改措辞）

**位置**
- 需求 `文档记录/需求文档/v1.6.0.md:747`：AC-R6「`jflove-app/` 与 `.github/` **零改动**
  （`git diff --name-only` 为空）」
- 实现：`git diff --name-only 049c20d..HEAD -- jflove-app` → **4 个文件**（`.github` → **0 个**）

**证据**

```powershell
git diff --name-only 049c20d..HEAD -- .github    # → 空（0 个）
git diff --name-only 049c20d..HEAD -- jflove-app # → README.md / lib/config/design_tokens.dart /
                                                 #   lib/pages/login/login_page.dart /
                                                 #   test/mermaid_security_test.dart（+9 −9）
```

改动内容**逐行核对**（`git diff 049c20d..HEAD -- jflove-app`）全部为**文档与注释**：
`README.md` 版本号 v1.5.0→v1.5.1、测试数 54→160、services 11→12、目录树说明；
`design_tokens.dart` 与 `login_page.dart` 各 1 行 **docstring 里的跨模块路径纠正**；
`mermaid_security_test.dart` 2 行注释。**零功能改动、零逻辑改动**。

**判定**：用户在 Phase 4a 清理批次已裁决 **D-A = 保留**（这 4 个文件随清理批次一并提交，commit `0f04ba2`）。
⇒ 记「**已裁决例外**」，不重复上报为缺陷（纪律⑤）；但**字面差异必须留痕**，否则 Phase 8 发布时
按 AC-R6 原文核对会得到"未通过"。

**建议**：Phase 7 落账时把 AC-R6 的措辞明确为"`.github/` 零改动；`jflove-app/` 除 D-A 裁决的
4 个文档/注释文件外零改动"（或把该 4 文件回退，若用户改主意）。

---

## 已核查、判定**不计缺陷**（覆盖留痕）

| 核查点 | 证据 | 判定 |
| --- | --- | --- |
| 桥零远端（AC-42 ①） | `src/bridge/editor.py` grep `/api/v1`、`http_client`、`requests` → 0 命中；三个新服务模块同样 0 命中 | 通过 |
| 桥改动面最小 | `git diff 049c20d..HEAD -- src/bridge/bridge.py` = **3 行**（import + `**editor_bridge.HANDLERS` + `\| editor_bridge.MAIN_THREAD_METHODS`），分发逻辑零改动 | 通过（既有桥零回归） |
| 主线程方法登记（设计 §11 P-3） | `src/bridge/editor.py:675` `MAIN_THREAD_METHODS` **恰好 5 项**（`editor.openDialog`/`detectAndRead`/`confirmQuit`/`cancelQuit`、`file_assoc.openSystemDefaults`），与 §7.1 一致；`QFileDialog` 只在 GUI 线程创建 | 通过 |
| 宿主动作清理（AC-10 实现侧） | `HOST_ACTIONS_TO_DISABLE`(`:110-126`) 按**具名枚举**白名单解析、跳过计数哨兵；`disable_host_actions()`(`:158`) 用 `pageAction(具名)` + `setShortcuts([])` + `setEnabled(False)`；与 `poc/editor-probes/R5-结论.md`（48 真实动作 + 1 哨兵、全 0 默认快捷键、三种关闭手段实测有效、`WebActionCount` 取 action 会 0xC0000005）一致 | 通过 |
| 退出握手状态机（AC-56 代码路径） | `request_quit()`(`:676`) 非 `idle` 幂等拒绝；`confirm_quit`/`cancel_quit` 非 `pending` 拒绝；三个内部信号跨线程排队到 GUI 线程(`:352-354`)；`QUIT_REQUEST_TIMEOUT_MS=5000` → `_fallback_confirm_quit()`(`:881`) 原生框默认/`Esc` 都是「取消」、接受则 `clean_exit=False` 保留草稿 | 通过（真机 GUI 待人工复核） |
| 脏计数口径 | `_resolve_dirty_count()`(`:797`) 取 `max(本地, 桥 get_quit_state())` —— 方向是"宁可多问一次" | 通过 |
| 关闭主窗口不清草稿（AC-58 代码路径） | `closeEvent`(`:1131`) 非退出流程一律 `event.ignore()` + `hide_to_tray()`；`ShellControls.close()`(`:244`) 同样只隐藏 | 通过 |
| 托盘退出不再绕过握手（设计 §6.4） | `src/ui/tray.py:97-99`：`quit_action.triggered` → `request_quit`（原 `quit_application`） | 通过 |
| `files.py` 改动 | `git diff` 确认**纯文档串**（纠正"`bridge.MAIN_THREAD_METHODS` 不存在"的说法） | 通过（零行为变化） |
| `reportDirty` 输入校验 | `src/bridge/editor.py:429-431`：`bool`/非数字 → `ValueError`；`int(raw)` 后 `max(0, …)`；与外壳 `report_dirty`(`:650-663`) 的"钳到 0"是**有意不同**（桥是外部输入入口，外壳是内部状态兜底） | 通过（**非**缺陷） |
| `confirmQuit` 的 pending 清除位置 | `editor.py:453-454`：`set_quit_pending(False)` 在 `confirm_quit()` **之后**（若其抛错则 pending 残留）。但外壳 `_do_confirm_quit` 自己先在 `:759` `_clear_bridge_pending()`，且残留态下 `request_quit()` 会因状态非 `idle` 拒绝重复请求 | 通过（影响面近零，不建议改） |

---

## AC 判定表（本面）

| AC | 判定 | 依据 |
| --- | --- | --- |
| AC-42 ① | ✅ 通过 | 桥 + 三个新服务模块远端关键字 0 命中 |
| AC-42 ② | ⚠ 部分（无头不可验） | 代码路径不依赖网络/登录（`load_ui()` 无登录前置、桥不查 session）；"断网 + 服务端未启动逐项可用"**需真机人工** |
| AC-46 ② | ❌ **不通过** | 见发现 2（设计 §6.6.3/§7.5 ③ 的 `restore_from_tray()` 未接线） |
| AC-47 | ✅ 通过（代码路径） | 编辑器桥/外壳不读 session，`load_ui()` 与登录无关；无阻塞式弹窗路径 |
| AC-56 | ✅ 通过（代码路径）+ 真机待验 | 状态机 / 超时兜底 / 取消不改内容 |
| AC-57 ③ | ✅ 通过（代码路径） | 保留选项只在 `QuitConfirmDialog` 一处（全局一次性） |
| AC-58 | ✅ 通过（代码路径） | `closeEvent` ignore + `hide_to_tray()`，不清草稿 |
| AC-R6（桌面既有行为） | ✅ 通过（本面部分） | `bridge.py` 3 行、`files.py` 文档串 |
| AC-R6（`jflove-app/`/`.github/` 零改动） | ⚠ **字面不通过 / 已裁决例外** | `.github` 0 个；`jflove-app` 4 个**文档与注释**文件（用户裁决 D-A 保留）——见上方 [轻微] 条目 |
| R-1（`Ctrl+S` 渲染层能否收到并 `preventDefault`） | ⚠ **无法验证（无头）** | `R5-结论.md`：本环境渲染层收不到任何注入键盘事件（QTest / Win32 `keybd_event` / 聚焦 / 去 `--disable-gpu` 全无效）⇒ 必须真机 GUI 复核 |

---

## 探针编写教训（供后续复现）

- `QWebChannel.registerObject(name, obj)` **不转移所有权**：写成 `registerObject("probe", Probe())`
  时 Python 引用为 0，对象被 GC 后渲染层一调用就**进程级 `0xC0000005`**（本次第一版探针 exit=`-1073741819`）；
  改为先绑定局部变量后即通过。同族现象见 BACKLOG **BL-16**。
- R7 探针与产物：`jflove-desktop/poc/editor-probes/r7-webengine-channel-origin.py`、
  `out/r7-offscreen.json`、`out/r7-offscreen.log`（**Phase 5 新增，未提交**）。

## 补充取证（主对话自查面，AC-R4 / §14.3 视角）

- 新增/变更测试文件清单（`git diff --stat 049c20d..HEAD -- jflove-desktop/tests jflove-server/tests` → **12 files / +4778 / −1**）：
  - 桌面端 9 个：`test_bridge_editor_shortcuts_v1_6_0.py`(+147)、`test_bridge_editor_v1_6_0.py`(+753)、`test_editor_service_v1_6_0.py`(+360)、`test_file_assoc_service_v1_6_0.py`(+322)、`test_gen_file_assoc_assets.py`(+428)、`test_scratch_service_v1_6_0.py`(+544)、`test_single_instance_v1_6_0.py`(+328)、`test_text_detect_service_v1_6_0.py`(+422)、`test_v160_web_shell_quit_handshake.py`(+885)；
  - 服务端 3 个：`test_upload_atomic_v1_6_0.py`(+371)、`test_upload_filename_traversal_v1_6_0.py`(+217)、`test_expected_schema_v1_5_0.py`(±1)。
- 测试文件里出现过的 AC 锚点（grep `AC-` 全量去重 **26 个**）：AC-10、AC-19、AC-20、AC-22、AC-38、AC-41、AC-42、AC-43、AC-44、AC-48、AC-49、AC-50、AC-50a、AC-51、AC-52、AC-53、AC-56、AC-58、AC-59、AC-60、AC-61、AC-B9、AC-S1、AC-S2、AC-S3、AC-S4。
- 设计 §14.3（`文档记录/设计文档/v1.6.0.md:1440-1442`）把 **40 条负向对照用例**判给 **Phase 6**：AC-8/10/11/12/13/18/20/25/26/27/28/29/31/32/33/34/35/36/37/40/41/42/43/44/46/47/50/52/53/54/58/61/S1/S2/S3/S4/B3/B4/B8/B9 ⇒ **Phase 5 不得因这些负向对照缺失而定级为缺陷**，只能记入「Phase 6 待补清单」（否则与设计分工冲突）。
- AC-42 与 r7 的关系：AC-42①（编辑器相关模块零远端调用）**成立且未被违反**；r7 暴露的是「渲染层只加载自家本地产物」这一**安全前提未固化**（§9.6），另立严重项，不并入 AC-42 判定。
- AC-46② 复核补证：`ui/src` 全量 grep `restore|showWindow|focusWindow|bringToFront|window.show` **无任何"请求主窗口可见"的桥调用**（命中全是 `auth-service.ts:123 restore()` 的会话恢复与 `scratch-store.ts:494` 的几何还原）；`src/` 全量 grep `restore_from_tray|showNormal|activateWindow|raise_\(` 显示 `restore_from_tray`（`web_shell.py:604-608` = `showNormal()`+`activateWindow()`+`raise_()`）**仅**被 `tray.py:76`（托盘菜单「显示窗口」）与 `tray.py:143`（托盘双击）调用 ⇒ **发现 2 成立**。
- 已核查**不计缺陷**：`AUTO_SAVE_DEBOUNCE_MS` 在 TS 侧不是"裸抄"——`ui/src/stores/scratch-store.ts:72-92` 有 `FALLBACK_AUTO_SAVE_DEBOUNCE_MS = 1500` 兜底，**优先读** `import.meta.env.VITE_AUTO_SAVE_DEBOUNCE_MS`（构建期注入，便于联调），且 `:19-21` docstring 明示与 `src/config/settings.py:106` 同值、依据 §6.2.2「前端防抖、Python 只做写入」⇒ 跨语言同值属**有据的重复**，登记为已核查项（非缺陷）。
- 已核查**不计缺陷**：`settings.py` 新增常量全部有消费方——`SCRATCH_DIR`(`scratch_service.py:85`)、`AUTO_SAVE_DEBOUNCE_MS`(TS 侧同值 + 文档)、`SHORTCUTS_FILE`(`bridge/editor.py:547-550`)、`FILE_ASSOC_FILE`(`file_assoc_service.py:154-158,288`)、`TEXT_MAX_BYTES`(`text_detect_service.py:39,486-490`)。

## 跨面复核取证（主对话独立复现，不采信子代理结论）

### R-A3-1（对应 A3 第 1 条，门禁判据）— **复现成立**
```powershell
$env:PATH = "C:\Windows\System32;C:\Windows"   # 隐藏 node
jflove-desktop\venv-win\Scripts\python.exe jflove-desktop\scripts\gen_file_assoc_assets.py --check
# [warn] 未找到 node：跳过 manifest ↔ scratch-types.ts 核对（只核对三处资产）
# [gen-file-assoc-assets] 一致：manifest（34 条）+ 三处生成物（jflove.xml / jflove.desktop / file-assoc.iss）
# exit=0
```

### R-A1-1（对应 A1 严重 1，`_safe_join` 同名前缀兄弟目录穿越）— **复现成立，且同类缺陷**有两份**实现**
```powershell
jflove-server\venv-win\Scripts\python.exe -c "from src.services.file_service import _safe_join; print(_safe_join(r'D:\disks\alice','../alice2/evil.txt'))"
# D:\disks\alice2\evil.txt        ← 越界放行
# '../bob/evil.txt' → PermissionError: 非法路径访问   ← 对照：不同前缀被拦
```
- 判据缺陷行：`jflove-server/src/services/file_service.py:106` = `if not str(target).startswith(str(base_path))`，`os.path.commonpath([r'D:\disks\alice2\evil.txt', r'D:\disks\alice'])}` = `D:\disks` ⇒ 正确判据应比 `commonpath` 或 `base + os.sep`。
- **同类实现重复**：`jflove-server/src/services/sync_service.py:26-39` 有一份**独立复制**的 `_safe_join`（`sync_service.py:26-39`），`:37` 同样是 `str(target).startswith(str(base_path))` ⇒ 同一缺陷、两处修（DRY 违反，建议合并为 `src/utils/path_safety.py` 单一实现）。

### R-A1-2（对应 A1 严重 2，`_validate_filename` 只有一处调用）— **复现成立**
`grep -rn "_validate_filename" jflove-server` ⇒ 定义 `file_service.py:115`、唯一调用 `file_service.py:283`（`init_upload` 内）、其余全在 `tests/test_upload_filename_traversal_v1_6_0.py`。`get_stream_range:703`、`download_file:462`、`delete_file:490`、`preview:549`、`rename:591`、`move:640-641`、`list:226`、`make_dir:522` 均**只经** `_safe_join`。
- ⇒ 计划文件 §17.8 曾写「`get_stream_range` 整体过了 `_safe_join` ⇒ 不可穿越」，该结论在"同名前缀兄弟目录"下**不成立**（本组复核推翻了 BL-10 结项时的"不可穿越"判断）；BL-10 的修复对 `init_upload` 路径有效，但**不能**推断其他 8 个端点安全。
