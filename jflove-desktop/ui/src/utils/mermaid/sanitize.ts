/**
 * Mermaid SVG 清洗
 *
 * mermaid 已经以 `securityLevel: 'strict'` 运行（禁 HTML 标签、禁 `javascript:` 链接），
 * 但 SVG 本身仍是注入面（`<script>`、`on*` 事件、外部 `href`）。
 * 在把 SVG 插入文档之前再做一次白名单式清理（安全宪法 §9.4：预览渲染不得成为注入面）。
 *
 * 为什么用正则而不是 DOMParser：
 *   这里处理的是**我们自己生成的**、结构可预期的 SVG 字符串，且清洗是"去掉危险构造"
 *   的保守操作；用 DOMParser + 白名单重建反而更容易漏掉命名空间细节。
 *   两种方式都不是唯一手段 —— 主防线是 `securityLevel: 'strict'`。
 */

/** 危险的属性名（事件处理器与脚本相关） */
const EVENT_ATTR = /\son[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi;

/** 外部/危险协议的引用（允许 data:image 与 # 内部引用） */
const DANGEROUS_HREF =
  /\s(?:xlink:href|href|src)\s*=\s*(?:"|')?\s*(?:javascript|vbscript|file):[^"'\s>]*(?:"|')?/gi;

/**
 * 带外部引用或危险协议的**整个标签**（兜底）。
 *
 * `href` / `xlink:href` 只是引用外部资源的其中一种写法：`<image src=...>`、
 * `<img src=...>` 同样能让渲染进程发起网络请求，只清理 href 会漏掉这一族。
 *
 * 注意这里**只删「带外部引用」的标签**，不能无差别删 `<image>`：
 * mermaid 的图标会用 `data:` 内联图片，那是本地的、必须保留
 * （`tests/utils/mermaid-security.test.ts` 有「不误伤正常图形」的断言锁着）。
 */
const EXTERNAL_TAG =
  /<[a-zA-Z][\w:.-]*\b[^>]*\s(?:xlink:href|href|src)\s*=\s*(?:"|')?\s*(?:https?:\/\/|\/\/)[^>]*>/gi;

/** 同上，但属性值**没有引号**（畸形 / 截断输入） */
const EXTERNAL_TAG_UNQUOTED =
  /<[a-zA-Z][\w:.-]*\b[^>]*\s(?:xlink:href|href|src)\s*=\s*(?:https?:\/\/|\/\/)[^\s>]*[^>]*>/gi;

/** 带危险协议的整个标签（兜底） */
const DANGEROUS_TAG =
  /<[a-zA-Z][\w:.-]*\b[^>]*\s(?:xlink:href|href|src)\s*=\s*(?:"|')?\s*(?:javascript|vbscript|file):[^>]*>/gi;

/** 外部 http(s) 引用（图表不应回连网络） */
const EXTERNAL_HREF =
  /\s(?:xlink:href|href|src)\s*=\s*(?:"|')?\s*https?:\/\/[^"'\s>]*(?:"|')?/gi;

/**
 * 清洗 SVG 字符串。
 *
 * 处理项：
 *   1. `<script>` / `<foreignObject>` / `<use>` / `<iframe>` 整体移除；
 *      `<script` 即使**没有闭合**（截断的源码、畸形输入）也要移除，否则它会
 *      把后面所有内容当成脚本吞掉；
 *   2. 全部 `on*` 事件属性；
 *   3. 带危险协议（`javascript:` / `vbscript:` / `file:`）的标签与属性；
 *   4. 带外部 `http(s)` / 协议相对 `//` 引用的标签与属性。
 *
 * 保留：`data:` 内联图片、`#` 内部锚点 —— 它们不联网，是 mermaid 的正常产物。
 */
export function sanitizeSvg(svg: string): string {
  let out = svg;

  // 1a. 危险元素整体移除（含内容）
  out = out.replace(/<script[\s\S]*?<\/script>/gi, '');
  out = out.replace(/<foreignObject[\s\S]*?<\/foreignObject>/gi, '');
  out = out.replace(/<iframe[\s\S]*?<\/iframe>/gi, '');

  // 1b. 未闭合的危险开标签：从 `<script` 一路删到行尾/末尾。
  //     顺序很重要 —— 必须在 `<\/script>` 那条之后，否则会把正常脚本的
  //     开始标签也吃掉（这里本来就是"删掉脚本"的语义，所以两条都要）。
  out = out.replace(/<script\b[\s\S]*$/gi, '');

  // 2. 事件属性
  out = out.replace(EVENT_ATTR, '');

  // 3. 带外部引用的整个标签（先整体删，避免留下结构残骸）
  out = out.replace(EXTERNAL_TAG, '');
  out = out.replace(EXTERNAL_TAG_UNQUOTED, '');

  // 4. 带危险协议的整个标签
  out = out.replace(DANGEROUS_TAG, '');

  // 5. 残留的危险协议属性
  out = out.replace(DANGEROUS_HREF, '');

  // 6. 残留的外部引用属性（属性值没引号、或标签形态没被上面命中）
  out = out.replace(EXTERNAL_HREF, '');

  return out;
}

/** 判断 SVG 是否仍含可执行构造或外部回连（供测试断言） */
export function svgHasDangerousContent(svg: string): boolean {
  return (
    // 注意用 \b 而不是 `>`：未闭合的 `<script` 同样是危险构造
    /<script\b/i.test(svg) ||
    /\son[a-z]+\s*=/i.test(svg) ||
    /(?:javascript|vbscript|file):/i.test(svg) ||
    // 任何指向远端或协议相对的资源引用
    /(?:xlink:href|href|src)\s*=\s*(?:"|')?\s*(?:https?:\/\/|\/\/)/i.test(svg)
  );
}
