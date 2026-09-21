/**
 * Markdown 工具栏 / 图表模板的「插入规划」
 *
 * 为什么单独抽成一个模块：这段逻辑曾经有一个**只影响 ER 图**的隐蔽缺陷，
 * 而且它藏在页面组件里、没有任何单测覆盖 —— 抽出来才能把这条约束锁死。
 *
 * ## 缺陷回顾（v1.5.0 反馈修复）
 *
 * 工具栏按钮需要"插入后把光标放到括号中间"，早期实现用 `|` 当**光标占位符**，
 * 并且**无条件**把插入文本里的第一个 `|` 当占位符：
 *
 * ```ts
 * const replacement = insert.includes('|') ? insert.replace('|', selected) : insert;
 * ```
 *
 * 而「ER 图」模板的正文里**本来就有** `|` —— mermaid 的 ER 关系语法是 `||--o{`。
 * 于是用户在**没有选中任何文字**时点「ER 图」，`selected` 是空串，
 * `replace('|', '')` 就把**第一根竖线删掉**，插入的源码变成：
 *
 * ```mermaid
 * erDiagram
 *     用户 |--o{ 会话 : 拥有     ← 只剩一根竖线
 * ```
 *
 * 而 mermaid 只认 `||--o{`，直接报 `Parse error on line 2: ... got '|'`。
 * 用户看到的就是「插入的样例代码有语法错误」，而模板常量本身完全正确 ——
 * 所以只检查模板常量的验证会漏掉它。
 *
 * ## 修法与约定
 *
 * - **默认原样插入**（`useCursorMarker` 默认 `false`）：图表模板走这条，正文里出现
 *   什么就插入什么，`|` 不会被特殊对待。**"默认不解析占位符"才是安全默认值**。
 * - 只有工具栏按钮显式声明 `useCursorMarker: true`，才把**第一个** `|` 当光标落点。
 * - 有选区时，选中的内容替换到占位符位置；没有选区时占位符只是被移除。
 */

/** 光标占位符（**仅**在 `useCursorMarker: true` 时才有特殊含义） */
export const CURSOR_MARKER = '|';

export interface InsertPlan {
  /** 要写入的文本（占位符已按选区替换/移除） */
  text: string;
  /** 插入后光标相对插入片段起点的偏移（0-based；等于 `text.length` 表示落在末尾） */
  cursorOffset: number;
}

/**
 * 规划一次插入。
 *
 * @param template        模板文本
 * @param selected        当前选中的文本（无选区传空串）
 * @param useCursorMarker 是否把第一个 `|` 当光标落点（**默认 false = 原样插入**）
 */
export function planInsertion(
  template: string,
  selected = '',
  useCursorMarker = false,
): InsertPlan {
  if (!useCursorMarker) {
    // 原样插入：光标落在插入内容之后
    return { text: template, cursorOffset: template.length };
  }
  const at = template.indexOf(CURSOR_MARKER);
  if (at < 0) {
    return { text: template, cursorOffset: template.length };
  }
  const head = template.slice(0, at);
  const tail = template.slice(at + 1);
  return {
    text: head + selected + tail,
    // 光标落在"被包住的内容之后"，也就是 head + selected 的长度处
    cursorOffset: head.length + selected.length,
  };
}
