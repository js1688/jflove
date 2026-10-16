/**
 * 编辑器内核（CM6）面板的**唯一中文短语表** —— 设计 v1.6.1 §15.2 / 需求 AC-74（① ② ③ ④ ⑤）
 *
 * ## 机制（已实测，设计 §15.0.1 M1 ~ M4）
 *
 * `@codemirror/search` 的 `SearchConfig` **没有** `phrases` 选项 —— 面板文案一律经
 * `view.state.phrase("…")` 取值（离线包里就是 `function dt(t,e){return t.state.phrase(e)}`）。
 * ⇒ **唯一正确做法是 `@codemirror/state` 的 `EditorState.phrases` facet**（`phrases.of({...})`）。
 * 父 agent 用 node 实跑现成离线包已证明：`search({phrases})` 是 **no-op**；
 * `EditorState.phrases.of({Find:'查找'})` 生效 ⇒ **可本地化、无需重打离线包**。
 *
 * ## 为什么是"零依赖叶子"
 *
 * ① 装配点有两个（`ScratchEditorHost` + `views/DiffView` 的两个独立 `EditorState`），必须**同一份**（DRY）；
 * ② 它不能 import `ScratchEditorHost`（否则与 `EditorPanel` / `DiffView` 形成环）；
 * ③ 只 `import type`（编译期擦除）⇒ **运行时零依赖**。
 *
 * ## 键集 = 实测全集（**31 条**，逐条给出取证方式）
 *
 * 复现命令（全在 `jflove-desktop/ui` 下执行）：
 *
 * ```powershell
 * $code = Get-Content -Raw public/vendor/editor.bundle.js
 * # ① 直接字面量：phrase("…")
 * [regex]::Matches($code, 'phrase\("([^"]*)"') | % { $_.Groups[1].Value } | Sort-Object -Unique
 * #    实测 17 条（与设计 §15.0.1 M4 逐字一致）：
 * #    $ unchanged lines / Accept / close / Completions / Control character / current match /
 * #    folded code / go / Go to line / on line / Reject / replaced $ matches /
 * #    replaced match on line $ / Revert this chunk / Selection deleted / to / unfold
 * [regex]::Matches($code, 'dt\(e,"([^"]*)"\)') | % { $_.Groups[1].Value } | Sort-Object -Unique
 * # ② 查找 / 替换面板：经 `dt(e,"…")`（= `state.phrase(键)`），实测 11 条：
 * #    Find / Replace / next / previous / all / match case / regexp / by word /
 * #    replace / replace all / close（close 与 ① 重复）
 * # ③ 折叠槽提示（三元表达式实参，正则抓不到字面量形式，需读上下文）：
 * #    this.open ? "Fold line" : "Unfold line"、O ? "Folded lines" : "Unfolded lines" —— 4 条
 * ```
 *
 * ⇒ 去重后 **31 条**。
 *
 * > ⚠ **与设计文档的口径偏差（如实登记，不改设计文档）**：设计 §15.2.1 的正文说
 * > "17 条（实测全集）"而示例对象里实际写了 **27 键**，本文件 = 设计的 27 键 **+** 折叠槽的
 * > 4 条提示（`Fold line` / `Unfold line` / `Folded lines` / `Unfolded lines`）= **31 键**。
 * > 多出的 4 条同样是**用户可见文案**（折叠箭头 `title` 与 `aria-live` 朗读文本），
 * > 不覆盖就会出现「AC-74 说 0 命中英文、但悬停折叠箭头仍是 `Fold line`」的判据缺口。
 *
 * - 键 = CM6 内核 `phrase("…")` 的**字面量**；**少一条就会有面板回落成英文**
 *   （`phrase()` 找不到键时**返回键本身**，M1 实测 ⇒ "漏翻译"表现为英文而不是空串）；
 * - 值里出现的 `$` 是 CM6 的**插值占位符**（M3 实测）：取值时由第二个实参替换，
 *   例：`phrase('$ unchanged lines', 7)` ⇒ `"未更改的 7 行"`。**不得**删掉 `$`，
 *   也不得写成 `$1` / `${n}`。
 *
 * ## 安全（AGENTS.md §9.14）
 *
 * 纯静态常量 + 纯函数：不读文件、不发请求、**零日志**、不含任何用户内容。
 */

import type { Extension, JFLoveCmApi } from '../../types/codemirror-bundle';

/**
 * CM6 内核面板的**唯一中文短语表**（AC-74）。
 *
 * ⚠ 修改本表 = 修改**用户可见文案**：新增键必须同时补 `__tests__/editor-phrases.test.ts`
 * 的覆盖清单（该用例逐键断言"译名不是英文原文"），否则漏掉的那条会静默回落成英文。
 */
export const EDITOR_PHRASES: Readonly<Record<string, string>> = Object.freeze({
  // ── 查找 / 替换面板（@codemirror/search，AC-74①②；键来自 `dt(e,"…")`，共 11 条）──
  Find: '查找',
  Replace: '替换',
  next: '下一个',
  previous: '上一个',
  all: '全选匹配',
  'match case': '区分大小写',
  regexp: '正则表达式',
  'by word': '全词匹配',
  replace: '替换',
  'replace all': '全部替换',
  close: '关闭',
  // ── 跳转行面板（AC-74②）──
  'Go to line': '跳转到行',
  go: '跳转',
  // ── 折叠占位符（AC-74③）──
  'folded code': '已折叠的代码',
  unfold: '展开',
  // ── 折叠槽的标题 / 朗读文本（AC-74③ 的同类可见文案；实测第 ③ 类）──
  'Fold line': '折叠此行',
  'Unfold line': '展开此行',
  'Folded lines': '已折叠',
  'Unfolded lines': '已展开',
  // ── 控制字符提示（AC-74④）──
  'Control character': '控制字符',
  // ── diff 折叠提示（AC-74⑤；`$` = 行数，见文件头）──
  '$ unchanged lines': '未更改的 $ 行',
  // ── 其余为"可见但未被 AC-74 逐项点名"的同类项，一并覆盖（零成本，防漏翻）──
  'Selection deleted': '已删除的选区',
  'current match': '当前匹配',
  'on line': '位于第',
  to: '至',
  Completions: '补全',
  'replaced $ matches': '已替换 $ 处匹配',
  'replaced match on line $': '已替换第 $ 行的匹配',
  Accept: '接受',
  Reject: '拒绝',
  'Revert this chunk': '还原此块',
});

/**
 * 装配用扩展（**防御式**：`phrases` facet 缺失时返回空数组，绝不抛）。
 *
 * 为什么不写成 `api.EditorState.phrases.of(...)` 直接调：离线包是**钉死版本 + 手写 .d.ts**，
 * 新旧包混用时 `phrases` 可能缺失（`load-editor-bundle.ts` 只校版本号，不保证 facet 存在）；
 * 更极端地，测试替身 / 半初始化的 API 上连 `EditorState` 都可能是空的。
 * 返回 `[]` 时行为 = 现状（面板英文），**不会崩**。
 *
 * 传**浅拷贝**而不是冻结表本身：`phrases.of()` 存的是**引用**（M1 实测）—— 传拷贝可避免
 * "某个消费方就地改字典"污染全局表；成本是一次 31 键的对象展开，只在挂载时发生。
 */
export function installEditorPhrases(api: JFLoveCmApi): Extension[] {
  const phrases = (
    api as { EditorState?: { phrases?: { of?: (value: Record<string, string>) => Extension } } } | null
  )?.EditorState?.phrases;
  if (!phrases || typeof phrases.of !== 'function') return [];
  return [phrases.of({ ...EDITOR_PHRASES })];
}
