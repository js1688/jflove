## 8. 无法验证项（无头环境限制）

| 项 | 关联 AC | 状态 | 说明 |
| --- | --- | --- | --- |
| `Ctrl+S` 能否被渲染层收到并 `preventDefault` | AC-10（残留） | **无法验证（无头）** | `poc/editor-probes/R5-结论.md` 实测：本机渲染层**收不到任何注入键盘事件**（QTest 与 Win32 `keybd_event`、hide/show、setFocus、去 `--disable-gpu` 全无效；鼠标可达）。**须真机 GUI 人工复核**（Phase 4a 唯一残留验收项 R-1）。兜底方向：宿主动作已实测可被 `setShortcuts([])` / `setEnabled(False)` / `removeAction()` 三种方式关闭（`R5-结论.md`），即"渲染层收不到"时宿主也不会抢键——但这**不等于**编辑器能拿到 `Ctrl+S`，两者需分别确认。 |
| 原生文件对话框（打开/另存为）交互 | AC-36 / AC-37 | **无法验证（无头）** | 桥侧用模块级接缝 `_open_file_names()` / `_save_file_name()` 做了可测化改造，行为由用例覆盖；**真实对话框**（默认目录、过滤器、取消路径）须真机复核。 |
| 托盘图标 / 托盘菜单交互 | AC-46②、AC-58 | **无法验证（无头）** | 代码路径经静态审查 + 用例覆盖（`request_quit` 改造、`closeEvent` ignore + `hide_to_tray`）；托盘双击/菜单真实点击须真机复核。 |
| 文件关联的**系统级**生效 | AC-43 / AC-44 | **部分无法验证** | 已实机验证：注册表 `SupportedTypes` 34 个空值、无新增 `.ext` 子键、`FileExts` 无变化、卸载后应用键消失、无 `_iu*.tmp` 残留；`iscc` 编译 exit 0、`/DAppExeName` 不匹配 → exit 2。**未验证**：双击文件后 Windows 是否真的用 JFLove 打开（依赖用户手动"设为默认"，属系统 UserChoice 范畴，本版按 U-13b C 方案不做）。 |
| 无头下的 `cm6` 真实输入/渲染 | AC-1 ~ AC-9 等 | **无法验证（无头）** | 分包产物结构、导出面、纯函数（`toolbox.ts` / `block-select.ts` / `formatShortcut`）均可离线真跑验证；真实光标/选区/输入法行为须真机复核。 |

## 9. 越界改动与已裁决例外

### 9.1 `jflove-app/`（4 文件，+9 / −9）

命令：`git diff --name-only 049c20d..HEAD -- jflove-app`

```
jflove-app/README.md
jflove-app/lib/config/design_tokens.dart
jflove-app/lib/pages/login/login_page.dart
jflove-app/test/mermaid_security_test.dart
```

- **违反**：`AGENTS.md §1` 模块边界（本版为桌面端 Phase 4a，不应改动移动端目录）。
- **实际内容**（逐行核对）：全部为**文档与注释**——版本号 v1.5.0→v1.5.1、测试数 54→160、services 11→12、两处 docstring 跨模块路径纠正、2 行注释。**零功能改动、零 Dart 代码逻辑改动**。
- **裁决**：用户已明确裁决 **D-A = 保留**（随清理批次提交 `0f04ba2`）。故此处记为**已裁决例外**，不再要求回滚。
- **建议**（Phase 7 / BACKLOG）：`AC-R6` 的措辞应明确为"除**用户已裁决的 D-A 例外**外，不越界改动其他模块"，避免下次审查出现同一处字面违反。

### 9.2 `.github/`（0 文件）

命令：`git diff --name-only 049c20d..HEAD -- .github` → **0 个**。符合 `AGENTS.md §1`「永远不要自动修改 `.github`」与 `AC-R6` 的字面要求。

### 9.3 其他受保护路径

`git diff --name-only 049c20d..HEAD -- .gitignore .vscode .idea` → **0 个**。`plans/`（`.gitignore:19`）与 `ui/public/vendor/`（`.gitignore:86`）为既有忽略规则，本版**未修改忽略文件**。
