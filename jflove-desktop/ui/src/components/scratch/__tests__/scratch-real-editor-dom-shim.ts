/**
 * **jsdom 测量 API 补丁**（测试专用，非应用源码）—— 本批 C1 的真宿主用例共用
 *
 * ## 为什么需要它
 *
 * 本批的 AC-77 / AC-78 / AC-74 用例刻意用**真** `ScratchEditorHost` + **真**离线包
 * （判据要求：正文必须活过 `view.destroy()`，替身宿主做不到）。而真宿主的 `baseExtensions`
 * 装的是**完整**扩展集（`foldGutter()` / `syntaxHighlighting` / `highlightSelectionMatches` /
 * `search({ top: true })` …），其中若干扩展在**测量**阶段会调 `range.getClientRects()`；
 * jsdom 的 `Range` **没有**该方法（实测 `typeof Range.prototype.getClientRects === 'undefined'`，
 * 真浏览器有）⇒ CM6 内部抛 `TypeError` 并被它自己的 `logException` 吞掉，
 * **每个用例在 stderr 里刷一串与判据无关的堆栈**（`Sn(...).getClientRects is not a function`）。
 *
 * ## 它**不**改变任何被断言的语义
 *
 * - 补的方法只是"返回空矩形 / 零矩形"（= 没有布局信息，与 jsdom 的真实能力一致）；
 * - 断言全部落在 `state.doc.toString()` / `state.selection` / `scrollDOM.scrollTop` /
 *   `state.phrase()` 这些**与布局无关**的量上；
 * - 副作用范围仅限**当前测试文件**的 jsdom 环境（vitest 默认按文件隔离环境）。
 *
 * 若哪天 jsdom 自己实现了这两个方法，本文件应当**整体删除**（而不是留成"永远正确的补丁"）。
 */

/** 给 `Range` 的测量方法装替身（幂等；已存在则不动 ⇒ 不为真浏览器语义背书） */
export function installJsdomRangeMeasureShim(): void {
  if (typeof Range === 'undefined') return;
  if (typeof Range.prototype.getClientRects !== 'function') {
    Range.prototype.getClientRects = function getClientRects(): DOMRectList {
      return Object.assign([], { item: () => null }) as unknown as DOMRectList;
    };
  }
  if (typeof Range.prototype.getBoundingClientRect !== 'function') {
    Range.prototype.getBoundingClientRect = function getBoundingClientRect(): DOMRect {
      return {
        x: 0,
        y: 0,
        width: 0,
        height: 0,
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        toJSON: () => ({}),
      } as DOMRect;
    };
  }
}
