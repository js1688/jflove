/**
 * 工具箱 12 项的**纯函数**实现（设计 v1.6.0 §4.1.6 / §3.3；AC-23 ~ AC-35）
 *
 * ## 本文件的定位
 *
 * 设计把工具箱拆成「纯函数层（`utils/scratch/transform/*.ts`）+ 派发层（`ScratchToolboxMenu`）」，
 * 本文件就是**纯函数层**：不 import 任何 CM6 包、不碰 DOM、不读写文件、无副作用，
 * 输入字符串/字符串数组，输出字符串/结果对象 —— Phase 6 可直接单测（ui 工程暂无测试运行器）。
 *
 * 需要运行期内核的能力（`sql-formatter` / `xml-formatter` / CM6 面板）**不在这里**：
 * 本文件只提供它们的**纯预检**与**默认参数**，实调用由 `ScratchToolbar` 从
 * `window.__JFLOVE_CM__`（`formatters.*`）取。这样"能不能格式化"的判据是可单测的纯函数，
 * "怎么格式化"才依赖离线包。
 *
 * ## 唯一真相：12 项（AC-23「恰好 12 项」）
 *
 * `TOOLBOX_ITEMS` 是本文件导出的**有序目录**，长度必须是 12；
 * `ScratchToolbar` **只按这份目录渲染**，不得自己再加一项。
 * 设计 §4.1.6 末尾的禁止项同样在这里兜住：**不含任何「时间戳 ↔ 日期互转」**（D10 的 0 命中判据）。
 *
 * ## 安全（AGENTS.md §9.6 / §9.14 / R-1）
 *
 * 本文件不打印任何内容（无 console），不访问 localStorage / DOM / 网络；
 * 错误信息只描述**形状**（位置、长度、原因），不回显文档内容。
 */

import type { SqlFormatOptions, XmlFormatOptions } from '../../types/codemirror-bundle';

/* ============================================================================
 * 第 2 项：行操作（排序 / 去重 / 去空行 / 去首尾空格）
 * 口径（§4.1.6）：以 `split('\n')` 的行数组为输入、`join('\n')` 输出，**不额外补末尾换行**
 * ========================================================================== */

/** 拆行（唯一口径：`split('\n')`；不吃 `\r`，CRLF 由调用方先归一化） */
export function splitLines(text: string): string[] {
  return text.split('\n');
}

/** 合行（唯一口径：`join('\n')`；末尾不补换行） */
export function joinLines(lines: readonly string[]): string {
  return lines.join('\n');
}

/**
 * 逐**码点**比较（`a < b` 逐码点，大小写敏感、与 locale 无关；§4.1.6 第 2 项）。
 *
 * 不用 `String.prototype.localeCompare`：它受运行环境 locale 影响，违反"与 locale 无关"。
 * 不用裸 `<`：那比较的是 UTF-16 code unit，星平面字符（U+10000+）会排错位置。
 */
export function compareByCodePoint(a: string, b: string): number {
  const left = Array.from(a);
  const right = Array.from(b);
  const shared = Math.min(left.length, right.length);
  for (let i = 0; i < shared; i += 1) {
    const ca = left[i].codePointAt(0) ?? 0;
    const cb = right[i].codePointAt(0) ?? 0;
    if (ca !== cb) return ca < cb ? -1 : 1;
  }
  return left.length - right.length;
}

/** 排序行：Unicode 码点升序、大小写敏感、与 locale 无关 */
export function sortLines(text: string): string {
  return joinLines([...splitLines(text)].sort(compareByCodePoint));
}

/** 去重行：完全相等保留首次出现 */
export function dedupeLines(text: string): string {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const line of splitLines(text)) {
    if (seen.has(line)) continue;
    seen.add(line);
    out.push(line);
  }
  return joinLines(out);
}

/** 去空行：**只删 `''`**；全空白行（`"   "`）不算空行，必须保留（§4.1.6 第 2 项） */
export function removeEmptyLines(text: string): string {
  return joinLines(splitLines(text).filter((line) => line !== ''));
}

/** 去首尾空格：每行 `trim()`（含 `\t`） */
export function trimLines(text: string): string {
  return joinLines(splitLines(text).map((line) => line.trim()));
}

/* ============================================================================
 * 第 3 项：大小写转换（大写 / 小写 / 驼峰 / 下划线）
 * 分隔符集合 = 空白符 / `_` / `-` / `.`（§4.1.6 第 3 项）
 * ========================================================================== */

/** 分词：按空白符 / `_` / `-` / `.` 切分，丢弃空词 */
export function splitWords(text: string): string[] {
  return text.split(/[\s_\-.]+/).filter((word) => word.length > 0);
}

/** 全大写（不做 locale 相关的特殊映射） */
export function toUpperCase(text: string): string {
  return text.toUpperCase();
}

/** 全小写 */
export function toLowerCase(text: string): string {
  return text.toLowerCase();
}

/**
 * 驼峰：切分后**首词全小写**、其余词首字母大写拼接。
 *
 * ⚠ 口径说明（设计原文只写了"首字母大写"）：其余词的**剩余部分按小写处理**，
 * 否则 `FOO_BAR` 会得到 `fooBAR`，与「下划线 = 切分后全小写」的兄弟口径不一致。
 */
export function toCamelCase(text: string): string {
  return splitWords(text)
    .map((word, index) => {
      const lower = word.toLowerCase();
      if (index === 0) return lower;
      return lower.charAt(0).toUpperCase() + lower.slice(1);
    })
    .join('');
}

/** 下划线：切分后全小写 + `_` 连接 */
export function toSnakeCase(text: string): string {
  return splitWords(text)
    .map((word) => word.toLowerCase())
    .join('_');
}

/* ============================================================================
 * 第 4 项：转义 / 反转义（JSON / HTML / URL）
 * ========================================================================== */

/**
 * JSON 转义：`"` → `\"`、`\` → `\\`、换行 → `\n`；**不含外层引号**（§4.1.6 第 4 项）。
 *
 * 设计只列了这三个；`\r` / `\t` **按原样保留**（不额外转义）—— 这是设计口径，不做"顺手增强"。
 */
export function escapeJson(text: string): string {
  let out = '';
  for (const ch of text) {
    if (ch === '\\') out += '\\\\';
    else if (ch === '"') out += '\\"';
    else if (ch === '\n') out += '\\n';
    else out += ch;
  }
  return out;
}

/** JSON 反转义（`escapeJson` 的逆操作；未知转义序列原样保留） */
export function unescapeJson(text: string): string {
  let out = '';
  for (let i = 0; i < text.length; i += 1) {
    if (text[i] !== '\\') {
      out += text[i];
      continue;
    }
    const next = text[i + 1];
    if (next === 'n') {
      out += '\n';
      i += 1;
    } else if (next === '"') {
      out += '"';
      i += 1;
    } else if (next === '\\') {
      out += '\\';
      i += 1;
    } else {
      out += '\\';
    }
  }
  return out;
}

/** HTML 转义：`& < > "`（`&` 必须最先替换，否则会二次转义） */
export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** HTML 反转义：只认设计列出的 4 个 + `&#39;` / `&apos;` */
export function unescapeHtml(text: string): string {
  return text.replace(/&(amp|lt|gt|quot|#39|apos);/g, (whole, name: string) => {
    switch (name) {
      case 'amp':
        return '&';
      case 'lt':
        return '<';
      case 'gt':
        return '>';
      case 'quot':
        return '"';
      case '#39':
      case 'apos':
        return "'";
      default:
        return whole;
    }
  });
}

/**
 * URL 百分号编码（UTF-8 字节）。
 *
 * 保留集合 = `A-Za-z0-9-_.!~*'()`（§4.1.6 第 4 项），其余字节一律 `%XX` **大写**。
 * 手写而不用 `encodeURIComponent`：后者的保留集合虽然一致，但显式实现才能在单测里逐字节核对，
 * 也避免将来运行环境改动其行为（例如 `!*'()` 在部分实现里被编码）。
 */
export function encodeUrlComponent(text: string): string {
  const unreserved = new Set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.!~*'()");
  const bytes = new TextEncoder().encode(text);
  let out = '';
  for (const byte of bytes) {
    const ch = String.fromCharCode(byte);
    out += unreserved.has(ch) ? ch : `%${byte.toString(16).toUpperCase().padStart(2, '0')}`;
  }
  return out;
}

/**
 * URL 百分号解码（UTF-8）。
 *
 * 非法百分号序列 / 非法 UTF-8 ⇒ **抛错**（调用方提示用户，禁止静默替换）。
 */
export function decodeUrlComponent(text: string): string {
  const bytes: number[] = [];
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (ch === '%') {
      const hex = text.slice(i + 1, i + 3);
      if (!/^[0-9a-fA-F]{2}$/.test(hex)) throw new Error('URL 解码失败：百分号转义不完整');
      bytes.push(Number.parseInt(hex, 16));
      i += 2;
      continue;
    }
    const encoded = new TextEncoder().encode(ch);
    for (const byte of encoded) bytes.push(byte);
  }
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(new Uint8Array(bytes));
  } catch {
    throw new Error('URL 解码失败：不是合法的 UTF-8 字节序列');
  }
}

/* ============================================================================
 * 第 5 项：Base64 编解码（UTF-8 往返，不用 btoa 直接吃字符串）
 * ========================================================================== */

/** UTF-8 文本 → Base64 */
export function encodeBase64(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let binary = '';
  // 分块拼接：避免 String.fromCharCode(...bigArray) 触发调用栈上限（大文档必踩）
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

/** Base64 → UTF-8 文本；字符集非法或字节序列不是合法 UTF-8 ⇒ **抛错** */
export function decodeBase64(text: string): string {
  const compact = text.replace(/\s+/g, '');
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(compact)) {
    throw new Error('Base64 解码失败：含非法字符');
  }
  let binary: string;
  try {
    binary = atob(compact);
  } catch {
    throw new Error('Base64 解码失败：长度或填充不合法');
  }
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch {
    throw new Error('Base64 解码失败：不是合法的 UTF-8 字节序列');
  }
}

/* ============================================================================
 * 第 6 项：JSON（格式化缩进 2 空格 / 压缩 / 树）
 * ⚠ 非法 JSON **只提示错误、原文逐字符不变**（§4.1.6 第 6 项）——
 *    所以这里的函数要么返回新串，要么抛错，**绝不返回"部分修复"的结果**。
 * ========================================================================== */

/** JSON 解析结果（树视图与格式化共用，避免各处重复 try/catch） */
export type JsonParseResult =
  | { ok: true; value: unknown }
  | { ok: false; error: string };

/**
 * 尝试解析 JSON。
 *
 * 失败信息**不含文档内容**：只给出行/列与原因（错误对象里的原文片段一律丢弃，§9.14）。
 */
export function tryParseJson(text: string): JsonParseResult {
  try {
    return { ok: true, value: JSON.parse(text) as unknown };
  } catch (error) {
    const message = error instanceof Error ? error.message : '';
    const position = /position\s+(\d+)/i.exec(message);
    const where = position ? `第 ${position[1]} 个字符附近` : '未知位置';
    return { ok: false, error: `JSON 解析失败（${where}）` };
  }
}

/** JSON 格式化（缩进 **2 空格**）；非法 JSON 抛错，原文不动 */
export function formatJson(text: string): string {
  const parsed = tryParseJson(text);
  if (!parsed.ok) throw new Error(parsed.error);
  return JSON.stringify(parsed.value, null, 2);
}

/** JSON 压缩（去结构外空白）；非法 JSON 抛错，原文不动 */
export function minifyJson(text: string): string {
  const parsed = tryParseJson(text);
  if (!parsed.ok) throw new Error(parsed.error);
  return JSON.stringify(parsed.value);
}

/* ============================================================================
 * 第 8 项：CSV / TSV（分隔符识别 + 只读表格）
 * 口径：`text.split(/\r?\n/)` + **首行分隔符投票**（`,` vs `\t`）；只读、**不回写**（§6.1.3）
 * ========================================================================== */

/** 支持的表格分隔符 */
export type CsvDelimiter = ',' | '\t';

/** 表格解析结果 */
export interface CsvTable {
  delimiter: CsvDelimiter;
  /** 行列矩阵（未对齐；列对齐由 `alignTableRows` 负责） */
  rows: string[][];
}

/**
 * 首行分隔符投票：在**第一行**上分别数"引号外"的 `,` 与 `\t`，多者胜出；**平票取 `,`**。
 *
 * 只在第一行投票（设计原话"首行分隔符投票"）：表头通常最能代表整表的分隔符，
 * 也避免正文里的逗号（例如 CSV 里的一列自由文本）把票数带偏。
 */
export function detectDelimiter(text: string): CsvDelimiter {
  const firstLine = text.split(/\r?\n/, 1)[0] ?? '';
  let commas = 0;
  let tabs = 0;
  let quoted = false;
  for (let i = 0; i < firstLine.length; i += 1) {
    const ch = firstLine[i];
    if (ch === '"') {
      if (quoted && firstLine[i + 1] === '"') {
        i += 1; // 引号内的转义引号
        continue;
      }
      quoted = !quoted;
      continue;
    }
    if (quoted) continue;
    if (ch === ',') commas += 1;
    else if (ch === '\t') tabs += 1;
  }
  return tabs > commas ? '\t' : ',';
}

/**
 * 解析定界文本（RFC4180 风格）：双引号包裹、`""` 表示字面引号、引号内可含换行。
 *
 * 末尾仅有一个空行（文档以换行结尾）时**丢弃该空行**，避免表格多出一行空行。
 */
export function parseDelimited(text: string, delimiter: CsvDelimiter = ','): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let quoted = false;
  let started = false;

  const endField = (): void => {
    row.push(field);
    field = '';
  };
  const endRow = (): void => {
    endField();
    rows.push(row);
    row = [];
  };

  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i += 1;
        } else {
          quoted = false;
        }
      } else {
        field += ch;
      }
      continue;
    }
    if (ch === '"' && field.length === 0) {
      quoted = true;
      started = true;
      continue;
    }
    if (ch === delimiter) {
      endField();
      started = true;
      continue;
    }
    if (ch === '\r') {
      if (text[i + 1] === '\n') i += 1;
      endRow();
      started = false;
      continue;
    }
    if (ch === '\n') {
      endRow();
      started = false;
      continue;
    }
    field += ch;
    started = true;
  }

  // 收尾：非空尾行 / 有字段在进行中才收；纯尾部换行不再多出一行
  if (started || field.length > 0 || row.length > 0) endRow();

  return rows;
}

/** 一次性解析：自动识别分隔符（视图首选入口） */
export function parseCsvTable(text: string, forced?: CsvDelimiter): CsvTable {
  const delimiter = forced ?? detectDelimiter(text);
  return { delimiter, rows: parseDelimited(text, delimiter) };
}

/** 列对齐：所有行补齐到最大列数（补 `''`）；**只读视图用，不回写原文** */
export function alignTableRows(rows: readonly string[][]): string[][] {
  const width = rows.reduce((max, row) => Math.max(max, row.length), 0);
  return rows.map((row) => {
    if (row.length >= width) return [...row];
    return [...row, ...new Array<string>(width - row.length).fill('')];
  });
}

/* ============================================================================
 * 第 9 项：SQL 美化（可配置）
 * 实调用 `window.__JFLOVE_CM__.formatters.sql`；本文件只给**纯预检**与默认参数。
 * 预检的 4 条来自 §4.1.6 的 R-6「已知不达标（须在 UI 侧规避）」：
 *   ① `DELIMITER` 会被改坏 ⇒ 禁用并提示；
 *   ② `:name` 命名参数在默认方言下抛错 ⇒ 提前拦；
 *   ③ 非 SQL 文本会被静默改写 ⇒ 轻量 SQL 形态预检；
 *   ④ 深层嵌套会长时间阻塞（1000 层实测 12.9 s）⇒ 输入规模 / 括号深度上限。
 * ========================================================================== */

/** SQL 美化输入长度上限（超过即拒绝，避免长阻塞） */
export const SQL_MAX_INPUT_LENGTH = 200_000;

/** SQL 美化括号嵌套深度上限（R-6 第 ④ 条） */
export const SQL_MAX_PAREN_DEPTH = 200;

/** SQL 可配置项（≥3 项：方言 / 关键字大小写 / 缩进宽度，§4.1.6 第 9 项） */
export interface SqlFormatConfig {
  language: string;
  keywordCase: 'preserve' | 'upper' | 'lower';
  tabWidth: number;
}

/** SQL 美化默认配置（关键字大写 + 2 空格缩进；R-6 定稿参数） */
export const DEFAULT_SQL_FORMAT_OPTIONS: SqlFormatOptions = {
  language: 'sql',
  keywordCase: 'upper',
  identifierCase: 'preserve',
  dataTypeCase: 'preserve',
  functionCase: 'preserve',
  indentStyle: 'standard',
  tabWidth: 2,
  useTabs: false,
  linesBetweenQueries: 1,
  expressionWidth: 50,
  denseOperators: false,
  newlineBeforeSemicolon: false,
};

/** 预检结果（`ok:false` 时 `reason` 是给用户看的中文提示，**不含文档内容**） */
export interface GuardResult {
  ok: boolean;
  reason?: string;
}

/** 轻量 SQL 形态关键词（形态预检用；不追求完备，只排除明显不是 SQL 的文本） */
const SQL_KEYWORD =
  /\b(select|insert|update|delete|merge|create|alter|drop|truncate|with|grant|revoke|explain|call|begin|commit|rollback)\b/i;

/** 数括号最大嵌套深度（跳过单/双引号与 `--` 行注释、`/* *\/` 块注释里的括号） */
function maxParenDepth(text: string): number {
  let depth = 0;
  let max = 0;
  let quote: string | null = null;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (quote) {
      if (ch === quote) quote = null;
      continue;
    }
    if (ch === "'" || ch === '"' || ch === '`') {
      quote = ch;
      continue;
    }
    if (ch === '-' && text[i + 1] === '-') {
      const nl = text.indexOf('\n', i);
      if (nl < 0) break;
      i = nl;
      continue;
    }
    if (ch === '/' && text[i + 1] === '*') {
      const end = text.indexOf('*/', i + 2);
      if (end < 0) break;
      i = end + 1;
      continue;
    }
    if (ch === '(') {
      depth += 1;
      if (depth > max) max = depth;
    } else if (ch === ')') {
      depth = Math.max(0, depth - 1);
    }
  }
  return max;
}

/**
 * SQL 美化前的纯预检：返回 `{ok:false, reason}` 时工具栏**必须禁用美化并显示提示**，
 * 不得调用 `formatters.sql`（否则会静默改写内容或长时间阻塞）。
 */
export function checkSqlFormattable(text: string): GuardResult {
  if (text.trim().length === 0) return { ok: false, reason: '内容为空，无需美化' };
  if (/^\s*delimiter\b/im.test(text)) {
    return { ok: false, reason: '含 DELIMITER（MySQL 客户端指令），美化会改坏原文，已禁用' };
  }
  if (text.length > SQL_MAX_INPUT_LENGTH) {
    return {
      ok: false,
      reason: `内容过长（${text.length} 字符，上限 ${SQL_MAX_INPUT_LENGTH}），已禁用美化`,
    };
  }
  if (maxParenDepth(text) > SQL_MAX_PAREN_DEPTH) {
    return { ok: false, reason: `括号嵌套过深（上限 ${SQL_MAX_PAREN_DEPTH} 层），已禁用美化` };
  }
  if (/(^|[^:\w]):[A-Za-z_]\w*/.test(text)) {
    return { ok: false, reason: '含命名参数（:name），所选方言不支持，已禁用美化' };
  }
  if (!SQL_KEYWORD.test(text)) {
    return { ok: false, reason: '未识别到 SQL 关键词，已禁用美化（避免静默改写非 SQL 文本）' };
  }
  return { ok: true };
}

/** 由可配置项合成 `sql-formatter` 选项（改配置后**重跑**格式化即"即时生效"） */
export function toSqlFormatOptions(config: SqlFormatConfig): SqlFormatOptions {
  return {
    ...DEFAULT_SQL_FORMAT_OPTIONS,
    language: config.language,
    keywordCase: config.keywordCase,
    tabWidth: Math.max(0, Math.trunc(config.tabWidth)),
    useTabs: false,
  };
}

/* ============================================================================
 * 第 10 项：XML 格式化 / **自研安全压缩**
 *
 * R-6 已实测（§4.1.6）：
 *   - `xml-formatter` 的 `minify` 会吃掉"元素唯一内容为纯空白文本"（`<a> </a>` → `<a></a>`），
 *     且 `collapseContent` 在多行混合内容上会**粘词** ⇒ 压缩**不用该库**，改自研；
 *   - 格式化必须 `strictMode:true` + `throwOnFailure:true`（否则非法 XML 被静默补全），
 *     且库会丢弃 BOM ⇒ **调用方补回**（本文件给 `hasBom` / `withBom` 两个纯助手）。
 *
 * 自研压缩的语义（AC-33：**压缩只删元素之间空白，不触碰文本节点内空白**）：
 *   ① 先做"纯空白文本节点"预检：某元素（不在 `xml:space="preserve"` 子树内）的**全部**子节点
 *      都是纯空白文本节点 ⇒ 这些空白是该元素**内容**，删了就是改内容 ⇒ **整体拒绝压缩**并给位置；
 *   ② 预检通过后，逐元素删除"父元素不含任何有意义子节点"的纯空白文本节点
 *      （即元素间的缩进空白）；混合内容（`<p>This is <b>x</b> content.</p>`）里的空白
 *      **一律保留**（父元素含非空白子节点）；
 *   ③ `xml:space="preserve"` 子树内**一律不删**；
 *   ④ 根元素之外的空白（XML 声明与根之间的换行等）**一律保留**（保守取舍）。
 * 与库的差异因此只可能出现在"库多删/粘词"的方向上，即**本实现更保守**。
 * ========================================================================== */

/** XML 元素内容的纯空白警告（拒绝压缩 / 格式化的依据） */
export interface XmlWhitespaceWarning {
  /** 元素路径（**只有标签名**，不含任何文本内容，§9.14） */
  path: string;
  /** 标签名 */
  tag: string;
  /** 空白文本的长度（不打印内容本身） */
  length: number;
}

/** XML 预检 / 压缩结果 */
export interface XmlResult {
  ok: boolean;
  /** `ok:true` 时有值 */
  output?: string;
  /** 给用户的中文提示（不含内容原文） */
  reason?: string;
  warnings: XmlWhitespaceWarning[];
}

/** XML 格式化选项（在 vendored `xml-formatter` 的选项上补 `attributeQuotes` / `strictMode`） */
export interface ScratchXmlFormatOptions extends XmlFormatOptions {
  attributeQuotes?: 'double' | 'single';
  strictMode?: boolean;
}

/** XML 格式化默认参数（R-6 定稿；BOM 由调用方用 `withBom` 补回） */
export const DEFAULT_XML_FORMAT_OPTIONS: ScratchXmlFormatOptions = {
  indentation: '  ',
  lineSeparator: '\n',
  attributeQuotes: 'double',
  collapseContent: true,
  strictMode: true,
  throwOnFailure: true,
};

/** 是否有 BOM（`xml-formatter` 会丢弃，格式化后需补回） */
export function hasBom(text: string): boolean {
  return text.charCodeAt(0) === 0xfeff;
}

/** 去掉 BOM */
export function stripBom(text: string): string {
  return hasBom(text) ? text.slice(1) : text;
}

/** 按需补回 BOM */
export function withBom(text: string, bom: boolean): string {
  return bom ? `\uFEFF${text}` : text;
}

/** 纯空白文本（`[ \t\r\n]*`，与 R-6 探针的 `WS` 正则同口径） */
const XML_WS = /^[ \t\r\n]*$/;

type XmlNode =
  | { kind: 'element'; name: string; open: string; close: string; selfClosing: boolean; children: XmlNode[]; preserve: boolean }
  | { kind: 'text'; raw: string }
  | { kind: 'raw'; raw: string };

interface XmlDocument {
  root: { kind: 'element'; name: string; open: string; close: string; selfClosing: boolean; children: XmlNode[]; preserve: boolean };
  /** 根元素之前 + 之后的原文（保守保留） */
  leading: string;
  trailing: string;
}

/** 从起始标签里取标签名（`<name ...` / `</name`） */
function readTagName(source: string): string {
  const match = /^<\/?\s*([^\s/>]+)/.exec(source);
  return match ? match[1] : '';
}

/** 解析属性里是否有 `xml:space="preserve"`（单双引号、无引号都认） */
function hasPreserveAttribute(tagSource: string): boolean {
  return /xml:space\s*=\s*("preserve"|'preserve'|preserve\b)/.test(tagSource);
}

/**
 * 轻量 XML 扫描（只建"元素 + 空白判定"所需的最小树，不做完整合法性校验）。
 *
 * 返回 `null` 表示结构不完整（标签未配对 / 提前闭合）——调用方给"XML 结构不完整"提示。
 */
function scanXml(body: string): XmlDocument | null {
  /** 根容器（虚拟文档节点，`preserve=false`，但不参与删除） */
  const documentRoot: XmlDocument['root'] = {
    kind: 'element',
    name: '#document',
    open: '',
    close: '',
    selfClosing: false,
    children: [],
    preserve: false,
  };
  const stack: XmlDocument['root'][] = [documentRoot];
  let leading = '';
  let i = 0;

  while (i < body.length) {
    if (body[i] !== '<') {
      const next = body.indexOf('<', i);
      const end = next < 0 ? body.length : next;
      const raw = body.slice(i, end);
      if (stack.length === 1) leading += raw;
      else stack[stack.length - 1].children.push({ kind: 'text', raw });
      i = end;
      continue;
    }
    // 注释 / CDATA / 处理指令 / DOCTYPE：整体当一个不可拆的原始节点
    const special = /^<!--|^<!\[CDATA\[|^<\?|^<!/.exec(body.slice(i));
    if (special) {
      const closer = special[0] === '<!--' ? '-->' : special[0] === '<![CDATA[' ? ']]>' : special[0] === '<?' ? '?>' : '>';
      const closeAt = body.indexOf(closer, i + special[0].length);
      if (closeAt < 0) return null;
      const raw = body.slice(i, closeAt + closer.length);
      const parent = stack[stack.length - 1];
      if (stack.length === 1) leading += raw;
      else parent.children.push({ kind: 'raw', raw });
      i = closeAt + closer.length;
      continue;
    }
    const tagEnd = body.indexOf('>', i);
    if (tagEnd < 0) return null;
    const tag = body.slice(i, tagEnd + 1);
    if (tag.startsWith('</')) {
      if (stack.length <= 1) return null;
      const top = stack.pop() as XmlDocument['root'];
      if (readTagName(tag) !== top.name) return null;
      top.close = tag;
      i = tagEnd + 1;
      continue;
    }
    const selfClosing = /\/>$/.test(tag);
    const parent = stack[stack.length - 1];
    const node: XmlDocument['root'] = {
      kind: 'element',
      name: readTagName(tag),
      open: tag,
      close: '',
      selfClosing,
      children: [],
      preserve: parent.preserve || hasPreserveAttribute(tag),
    };
    parent.children.push(node);
    if (!selfClosing) stack.push(node);
    i = tagEnd + 1;
  }
  if (stack.length !== 1) return null; // 有未闭合标签

  const rootElement = documentRoot.children.find(
    (child): child is XmlDocument['root'] => child.kind === 'element',
  );
  if (!rootElement) return null;
  const rootIndex = documentRoot.children.indexOf(rootElement);
  const trailing = documentRoot.children
    .slice(rootIndex + 1)
    .filter((child) => child.kind === 'text' && child.raw)
    .map((child) => (child.kind === 'text' ? child.raw : ''))
    .join('');
  return { root: rootElement, leading, trailing };
}

/**
 * 元素是否含"有意义"子节点。
 *
 * 判据与 R-6 探针 `findContentWhitespace` 一致：**只有纯空白文本节点不算有意义**；
 * 元素 / 注释 / CDATA / 处理指令 / 含非空白的文本都算有意义。
 */
function hasMeaningfulChild(element: XmlDocument['root']): boolean {
  return element.children.some(
    (child) => !(child.kind === 'text' && XML_WS.test(child.raw)),
  );
}

/** 递归收集"元素内容为纯空白"的警告（`xml:space="preserve"` 子树跳过） */
function collectContentWhitespace(
  element: XmlDocument['root'],
  path: string,
  out: XmlWhitespaceWarning[],
): void {
  const meaningful = hasMeaningfulChild(element);
  if (!element.preserve && element.children.length > 0 && !meaningful) {
    for (const child of element.children) {
      if (child.kind !== 'text') continue;
      out.push({ path, tag: element.name, length: child.raw.length });
    }
  }
  for (const child of element.children) {
    if (child.kind === 'element') {
      collectContentWhitespace(child, `${path}/${child.name}`, out);
    }
  }
}

/** 序列化：删除"父元素无有意义子节点"的纯空白文本节点（其余原样回写） */
function serializeElement(element: XmlDocument['root']): string {
  if (element.selfClosing) return element.open;
  const meaningful = hasMeaningfulChild(element);
  const inner = element.children
    .map((child) => {
      if (child.kind === 'text') {
        if (!element.preserve && !meaningful && XML_WS.test(child.raw)) return '';
        return child.raw;
      }
      if (child.kind === 'raw') return child.raw;
      return serializeElement(child);
    })
    .join('');
  return `${element.open}${inner}${element.close || `</${element.name}>`}`;
}

/**
 * XML 压缩前预检（纯函数）。
 *
 * `ok:false` 时工具栏**必须拒绝压缩**并显示 `reason`（AC-33：绝不静默改内容）。
 */
export function checkXmlMinifiable(text: string): XmlResult {
  const scanned = scanXml(stripBom(text));
  if (!scanned) {
    return { ok: false, reason: 'XML 结构不完整（标签未配对），已拒绝压缩', warnings: [] };
  }
  const warnings: XmlWhitespaceWarning[] = [];
  collectContentWhitespace(scanned.root, `/${scanned.root.name}`, warnings);
  if (warnings.length > 0) {
    return {
      ok: false,
      reason: 'XML 含"元素内容为纯空白"的文本节点，压缩会改变内容，已拒绝',
      warnings,
    };
  }
  return { ok: true, warnings: [] };
}

/**
 * **自研安全压缩**（不使用 `xml-formatter.minify`，理由见本段文件头）。
 *
 * 语义 = 预检 + 只删元素间空白。BOM 原样保留（压缩不改编码形态）。
 */
export function xmlSafeMinify(text: string): XmlResult {
  const bom = hasBom(text);
  const guard = checkXmlMinifiable(text);
  if (!guard.ok) return { ...guard, output: undefined };
  const scanned = scanXml(stripBom(text));
  if (!scanned) {
    return { ok: false, reason: 'XML 结构不完整（标签未配对），已拒绝压缩', warnings: [] };
  }
  const output = withBom(scanned.leading + serializeElement(scanned.root) + scanned.trailing, bom);
  return { ok: true, output, warnings: [] };
}

/**
 * XML 格式化前预检（纯函数）。
 *
 * 与 `checkXmlMinifiable` 的差别：格式化允许 `collapseContent:true`（保住已存在的文本节点），
 * 但同样**拒绝**"元素内容为纯空白"的输入 —— 理由同 R-6 探针的 `safeFormat(blockOnUnsafe=true)`：
 * 这类输入下格式化会改动文本节点，而 AC-33 要求"不触碰文本节点内空白"。
 */
export function checkXmlFormattable(text: string): XmlResult {
  const guard = checkXmlMinifiable(text);
  if (!guard.ok) return guard;
  return { ok: true, warnings: [] };
}

/** 把格式化结果按需补回 BOM（`xml-formatter` 会丢 BOM） */
export function restoreBomForFormatted(source: string, formatted: string): string {
  return withBom(formatted, hasBom(source));
}

/* ============================================================================
 * 第 12 项：字号缩放 + 行号 / 自动换行开关（纯函数部分）
 * ========================================================================== */

/** 字号下限（与 `scratch-store.ts` 的钳制范围一致） */
export const FONT_SIZE_MIN = 8;

/** 字号上限（与 `scratch-store.ts` 的钳制范围一致） */
export const FONT_SIZE_MAX = 40;

/** 字号档位钳制（整数化 + 上下限） */
export function clampFontSize(size: number): number {
  if (!Number.isFinite(size)) return FONT_SIZE_MIN;
  return Math.min(FONT_SIZE_MAX, Math.max(FONT_SIZE_MIN, Math.round(size)));
}

/** 按步长缩放字号（`Ctrl` + 滚轮 / 加减按钮共用） */
export function stepFontSize(size: number, delta: number): number {
  return clampFontSize(size + delta);
}

/* ============================================================================
 * 12 项目录（AC-23 的唯一真相）
 * ========================================================================== */

/** 工具箱项 id（**恰好 12 个**，顺序即设计 §4.1.6 的 1~12） */
export type ToolboxItemId =
  | 'search'
  | 'lines'
  | 'case'
  | 'escape'
  | 'base64'
  | 'json'
  | 'markdown'
  | 'csv'
  | 'sql'
  | 'xml'
  | 'diff'
  | 'font';

/** 工具箱项的形态（决定 UI 怎么派发） */
export type ToolboxItemKind =
  /** 打开 CM6 面板（查找 / 替换 / 跳转行） */
  | 'panel'
  /** 纯文本变换（读写正文） */
  | 'transform'
  /** 切换视图模式（编辑 / 预览 / 分屏 / 表格 / 树 / 对比） */
  | 'view'
  /** 编辑偏好（字号 / 行号 / 自动换行） */
  | 'preference';

/** 单个工具箱项 */
export interface ToolboxItem {
  id: ToolboxItemId;
  /** 1 基序号（与设计表格逐行对齐，便于核对"恰好 12 项"） */
  index: number;
  /** 菜单显示名（设计原文） */
  label: string;
  kind: ToolboxItemKind;
  /** 一句话说明（菜单副标题；不含任何用户内容） */
  hint: string;
}

/**
 * 工具箱目录 —— **恰好 12 项**（AC-23）。
 *
 * 判据：`TOOLBOX_ITEMS.length === 12` 且 `index` 为 1..12 连续。
 * ⚠ 不得新增第 13 项；也不得加入"时间戳 ↔ 日期互转"（D10 的 0 命中判据）。
 */
export const TOOLBOX_ITEMS: readonly ToolboxItem[] = [
  {
    id: 'search',
    index: 1,
    label: '查找 / 替换 / 跳转行',
    kind: 'panel',
    hint: '正则匹配与替换；跳转行按 1 基行号',
  },
  {
    id: 'lines',
    index: 2,
    label: '行操作',
    kind: 'transform',
    hint: '排序（码点升序）/ 去重 / 去空行 / 去首尾空格',
  },
  { id: 'case', index: 3, label: '大小写转换', kind: 'transform', hint: '大写 / 小写 / 驼峰 / 下划线' },
  { id: 'escape', index: 4, label: '转义 / 反转义', kind: 'transform', hint: 'JSON / HTML / URL' },
  { id: 'base64', index: 5, label: 'Base64 编解码', kind: 'transform', hint: 'UTF-8 往返' },
  {
    id: 'json',
    index: 6,
    label: 'JSON 格式 / 压缩 / 树',
    kind: 'transform',
    hint: '缩进 2 空格；非法 JSON 只提示，原文不动',
  },
  {
    id: 'markdown',
    index: 7,
    label: 'Markdown 编辑 / 预览 / 分屏',
    kind: 'view',
    hint: '预览经 marked + DOMPurify 清洗',
  },
  { id: 'csv', index: 8, label: 'CSV / TSV 表格视图', kind: 'view', hint: '首行分隔符投票；只读不回写' },
  { id: 'sql', index: 9, label: 'SQL 美化', kind: 'transform', hint: '可配方言 / 关键字大小写 / 缩进宽度' },
  {
    id: 'xml',
    index: 10,
    label: 'XML 格式化 / 压缩',
    kind: 'transform',
    hint: '严格模式格式化 + 自研安全压缩',
  },
  {
    id: 'diff',
    index: 11,
    label: '两段文本对比',
    kind: 'view',
    hint: '第二段来源：剪贴板 / 另一窗格 / 本地文件',
  },
  {
    id: 'font',
    index: 12,
    label: '字号缩放 / 行号 / 自动换行',
    kind: 'preference',
    hint: 'Ctrl + 滚轮缩放字号；行号与自动换行可控',
  },
];

/** 工具箱项数（AC-23 判据：必须等于 12） */
export const TOOLBOX_ITEM_COUNT = TOOLBOX_ITEMS.length;
