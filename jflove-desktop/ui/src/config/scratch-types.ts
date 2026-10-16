/**
 * 文本编辑器类型表 —— 扩展名到语言 / 视图 / 能力的**唯一真相**（设计 v1.6.0 §5.3 + §5.1 AC-48a）
 *
 * ## 为什么是「唯一真相」
 *
 * 本文件的 `TYPES` 是**唯一手写清单**：
 *   - `jflove-desktop/scripts/gen_file_assoc_manifest.mjs` 在构建期（由 build.py 调用）**只读本文件**，
 *     派生 `ui/public/file-assoc.manifest.json`，再由 Python 侧 `file_assoc_service` 生成注册项；
 *   - 设置页「文件关联」勾选清单、语言切换下拉、能力表**运行时 import 本文件**；
 *   - 任何 Python 文件 / .iss / .desktop **都不得**再手打扩展名清单（§5.1 反例禁止）。
 *
 * ## ⚠ TYPES 字面量必须是**合法 JSON**（供 gen_file_assoc_manifest.mjs 文本派生）
 *
 * Node 不能直接 import .ts，派生脚本是「文本提取 + JSON.parse」，因此：
 *   1. `TYPES` 必须是顶层声明，且它的数组字面量是本文件中**第一个左方括号**所在处
 *      —— 所以本声明用了 `Array<TypeEntry>` 而不是 `TypeEntry` 加方括号后缀的写法：
 *      后者注解里的方括号会抢在字面量之前，破坏「首个左方括号到匹配的右方括号」定位。
 *      改动本声明前，请确认**它上方没有任何方括号字符**（含注释）；
 *   2. 字面量内部：键名与字符串值一律双引号、**无尾逗号**、**无注释**、**无单引号**、
 *      无表达式 / 函数调用 / 展开运算符 / 类型断言 / 模板串；
 *   3. **每项只允许 `ext` / `label` / `language` / `mime` 四个字段**（`mime` 为字符串数组），
 *      与 §5.1 的 manifest 结构逐字对齐（键名 `ext`、`label`、`language`、`mime`）；
 *   4. 任何"顺手补注释 / 加尾逗号 / 加字段"都会**静默打断派生链**（CI 判据
 *      `python scripts/check_file_assoc_manifest.py --check` 会红），改前请先读本节。
 *
 * 需要给后人看的行来源说明、能力表等，**一律放在字面量之外**（见下方 TYPE_DETAIL_GROUPS）。
 */

import type { ScratchViewMode } from '../types/scratch';

export const TYPES: Array<TypeEntry> = [
  { "ext": "json", "label": "JSON", "language": "json", "mime": ["application/json"] },
  { "ext": "jsonc", "label": "带注释的 JSON", "language": "json", "mime": ["application/json"] },
  { "ext": "md", "label": "Markdown", "language": "markdown", "mime": ["text/markdown"] },
  { "ext": "markdown", "label": "Markdown", "language": "markdown", "mime": ["text/markdown"] },
  { "ext": "csv", "label": "CSV", "language": "text", "mime": ["text/csv"] },
  { "ext": "tsv", "label": "TSV", "language": "text", "mime": ["text/csv"] },
  { "ext": "sql", "label": "SQL", "language": "sql", "mime": ["application/sql"] },
  { "ext": "xml", "label": "XML", "language": "xml", "mime": ["application/xml"] },
  { "ext": "html", "label": "HTML", "language": "html", "mime": ["text/html"] },
  { "ext": "svg", "label": "SVG", "language": "xml", "mime": ["image/svg+xml"] },
  { "ext": "yaml", "label": "YAML", "language": "yaml", "mime": ["application/yaml"] },
  { "ext": "yml", "label": "YAML", "language": "yaml", "mime": ["application/yaml"] },
  { "ext": "toml", "label": "TOML", "language": "text", "mime": ["text/plain"] },
  { "ext": "ini", "label": "INI", "language": "text", "mime": ["text/plain"] },
  { "ext": "conf", "label": "配置文件 (conf)", "language": "text", "mime": ["text/plain"] },
  { "ext": "properties", "label": "属性文件", "language": "text", "mime": ["text/plain"] },
  { "ext": "css", "label": "CSS", "language": "css", "mime": ["text/css"] },
  { "ext": "py", "label": "Python", "language": "python", "mime": ["text/x-python"] },
  { "ext": "js", "label": "JavaScript", "language": "javascript", "mime": ["text/javascript"] },
  { "ext": "ts", "label": "TypeScript", "language": "typescript", "mime": ["text/typescript"] },
  { "ext": "tsx", "label": "TSX", "language": "typescript", "mime": ["text/typescript"] },
  { "ext": "jsx", "label": "JSX", "language": "javascript", "mime": ["text/javascript"] },
  { "ext": "sh", "label": "Shell 脚本", "language": "shell", "mime": ["application/x-shellscript"] },
  { "ext": "bat", "label": "批处理 (bat)", "language": "batch", "mime": ["text/plain"] },
  { "ext": "ps1", "label": "PowerShell", "language": "powershell", "mime": ["text/plain"] },
  { "ext": "java", "label": "Java", "language": "java", "mime": ["text/x-java"] },
  { "ext": "go", "label": "Go", "language": "go", "mime": ["text/x-go"] },
  { "ext": "rs", "label": "Rust", "language": "rust", "mime": ["text/rust"] },
  { "ext": "c", "label": "C", "language": "c", "mime": ["text/x-csrc"] },
  { "ext": "cpp", "label": "C++", "language": "cpp", "mime": ["text/x-c++src"] },
  { "ext": "log", "label": "日志 (log)", "language": "text", "mime": ["text/plain"] },
  { "ext": "txt", "label": "纯文本 (txt)", "language": "text", "mime": ["text/plain"] },
  { "ext": "diff", "label": "差异", "language": "diff", "mime": ["text/x-diff"] },
  { "ext": "patch", "label": "补丁", "language": "diff", "mime": ["text/x-diff"] }
];

/* ────────────────────────────────────────────────────────────────────────────
 * 以下为类型定义与派生量：**不参与**文本派生，可以自由使用 TS 语法。
 *
 * ## TYPES 与 §5.3 表格行的对应关系（逐行核对用）
 *
 * | §5.3 行 | TYPES 中的 ext | 能力组（TYPE_DETAIL_GROUPS 的 group） |
 * | --- | --- | --- |
 * | 1 json / jsonc | json, jsonc | `json` |
 * | 2 md / markdown | md, markdown | `markdown` |
 * | 3 csv / tsv | csv, tsv | `csv` |
 * | 4 sql | sql | `sql` |
 * | 5 xml / html / svg | xml, html, svg | `xml` / `html` / `svg` |
 * | 6 yaml / yml / toml / ini / conf / properties / css | yaml, yml, toml, ini, conf, properties, css |
 * | 7 py js ts tsx jsx sh bat ps1 java go rs c cpp | 同名列 | `code`（各自语言包） |
 * | 8 log / txt | log, txt | `plain` |
 * | 9 diff / patch | diff, patch | `diff`（强制只读） |
 * | 10 未知扩展名（文本内容） | **不进 TYPES** | `UNKNOWN_TEXT_TYPE`（兜底条目，见下） |
 * | 11 未知扩展名（二进制内容） | **不进 TYPES** | `BINARY_REJECT_POLICY`（不加载、不产生窗格） |
 * | 8 的行内「无扩展名」 | **不进 TYPES** | `NO_EXTENSION_TEXT_TYPE`（无 ext，不可进关联清单） |
 *
 * 依据 §5.3 的 D23：关联清单 = **有明确 ext 的条目**（即 TYPES 全部内容）；
 * 「未知扩展名」两行与「无扩展名」是**兜底策略**而非"支持的格式"，故不进清单。
 * ──────────────────────────────────────────────────────────────────────────── */

/** manifest 单条 = §5.1 的 `{"ext","label","language","mime"}`（TYPES 的元素形状） */
export interface TypeEntry {
  /** 扩展名，小写、**不含点**、不含通配（如 `json`） */
  ext: string;
  /** 设置页与系统关联项显示的可读名称 */
  label: string;
  /** 语言 id（对应 CM6 语言包；`text` = 纯文本无语法解析） */
  language: ScratchLanguage;
  /** 供文件关联派生用的 MIME（§5.1 的 `mime` 字段，值即字符串数组） */
  mime: string[];
}

/** 语言 id（对应 CM6 语言包；`text` = 无语法解析的纯文本） */
export type ScratchLanguage =
  | 'json'
  | 'markdown'
  | 'text'
  | 'sql'
  | 'xml'
  | 'html'
  | 'yaml'
  | 'css'
  | 'python'
  | 'javascript'
  | 'typescript'
  | 'shell'
  | 'batch'
  | 'powershell'
  | 'java'
  | 'go'
  | 'rust'
  | 'c'
  | 'cpp'
  | 'diff';

/** 高亮档位（§5.3 的「高亮」列） */
export type ScratchHighlight = 'full' | 'table' | 'plaintext' | 'none';

/** 差异化能力（§5.3 的 `capabilities` 列，值逐项对齐，不自创） */
export type ScratchCapability =
  | 'format'
  | 'minify'
  | 'tree'
  | 'fold'
  | 'preview'
  | 'split'
  | 'table'
  | 'delimiter-detect'
  | 'align'
  | 'sqlFormat'
  | 'indent'
  | 'bracketMatch'
  | 'autoIndent'
  | 'commentToggle'
  | 'monospace'
  | 'wordWrap'
  | 'largeFileGuard';

/** 单个扩展名的完整类型信息（= TYPES 条目 + 能力组，供 UI 与 store 使用） */
export interface ScratchExtInfo extends TypeEntry {
  /** 能力组 id（= §5.3 的表格行标识） */
  group: string;
  capabilities: ScratchCapability[];
  highlight: ScratchHighlight;
  /** 该类型默认打开的视图模式（可在能力允许范围内被用户切换） */
  defaultViewMode: ScratchViewMode;
  /** 该类型是否强制只读（§5.3 第 9 行：diff / patch） */
  readOnly: boolean;
  /** 全角说明（设置页 / 语言下拉的辅助文案） */
  description: string;
}

interface TypeDetail {
  group: string;
  capabilities: ScratchCapability[];
  highlight: ScratchHighlight;
  defaultViewMode: ScratchViewMode;
  readOnly: boolean;
  description: string;
}

/**
 * 能力组表：把 §5.3 的「差异化能力 / 高亮 / MIME 之外的属性」按**表格行**组织，
 * 再摊到 TYPES 的每个扩展名上。改这里不会影响 manifest 派生（派生只看 TYPES）。
 */
export const TYPE_DETAIL_GROUPS: Array<{ exts: string[]; detail: TypeDetail }> = [
  {
    exts: ['json', 'jsonc'],
    detail: {
      group: 'json',
      capabilities: ['format', 'minify', 'tree', 'fold'],
      highlight: 'full',
      defaultViewMode: 'edit',
      readOnly: false,
      description: 'JSON / JSONC：格式化、压缩、JSON 树（只读视图）、折叠，非法 JSON 只提示不改原文',
    },
  },
  {
    exts: ['md', 'markdown'],
    detail: {
      group: 'markdown',
      capabilities: ['preview', 'split'],
      highlight: 'full',
      defaultViewMode: 'edit',
      readOnly: false,
      description: 'Markdown：编辑 / 预览 / 分屏三态循环切换',
    },
  },
  {
    exts: ['csv', 'tsv'],
    detail: {
      group: 'csv',
      capabilities: ['table', 'delimiter-detect', 'align'],
      highlight: 'table',
      defaultViewMode: 'table',
      readOnly: false,
      description: 'CSV / TSV：分隔符识别（, 与 \\t）、列对齐的**只读**表格视图，不回写',
    },
  },
  {
    exts: ['sql'],
    detail: {
      group: 'sql',
      capabilities: ['sqlFormat'],
      highlight: 'full',
      defaultViewMode: 'edit',
      readOnly: false,
      description: 'SQL：美化可配方言 / 关键字大小写 / 缩进宽度；含 DELIMITER 等不达标输入时禁用并提示',
    },
  },
  {
    exts: ['xml'],
    detail: {
      group: 'xml',
      capabilities: ['format', 'minify', 'fold', 'indent'],
      highlight: 'full',
      defaultViewMode: 'edit',
      readOnly: false,
      description: 'XML：格式化（严格模式）+ 自研安全压缩（保留注释与 CDATA、纯空白文本节点预检）',
    },
  },
  {
    exts: ['html'],
    detail: {
      group: 'html',
      capabilities: ['format', 'minify', 'fold', 'indent'],
      highlight: 'full',
      defaultViewMode: 'edit',
      readOnly: false,
      description: 'HTML：格式化 / 压缩 / 折叠 / 缩进',
    },
  },
  {
    exts: ['svg'],
    detail: {
      group: 'svg',
      capabilities: ['format', 'minify', 'fold', 'indent'],
      highlight: 'full',
      defaultViewMode: 'edit',
      readOnly: false,
      description: 'SVG：按 XML 语言解析（§5.3 的语言列为 xml），支持格式化 / 压缩 / 折叠 / 缩进',
    },
  },
  {
    exts: ['yaml', 'yml'],
    detail: {
      group: 'yaml',
      capabilities: ['fold', 'indent'],
      highlight: 'full',
      defaultViewMode: 'edit',
      readOnly: false,
      description: 'YAML：折叠 / 缩进',
    },
  },
  {
    exts: ['toml'],
    detail: {
      group: 'config-plain',
      capabilities: ['fold', 'indent'],
      highlight: 'full',
      defaultViewMode: 'edit',
      readOnly: false,
      description: 'TOML：折叠 / 缩进（语言设为纯文本，无专用语言包）',
    },
  },
  {
    exts: ['ini'],
    detail: {
      group: 'config-plain',
      capabilities: ['fold', 'indent'],
      highlight: 'full',
      defaultViewMode: 'edit',
      readOnly: false,
      description: 'INI：折叠 / 缩进（语言设为纯文本）',
    },
  },
  {
    exts: ['conf'],
    detail: {
      group: 'config-plain',
      capabilities: ['fold', 'indent'],
      highlight: 'full',
      defaultViewMode: 'edit',
      readOnly: false,
      description: 'conf：按 §5.3 口径用「text + 高亮」，无对应语言包时退化为纯文本',
    },
  },
  {
    exts: ['properties'],
    detail: {
      group: 'config-plain',
      capabilities: ['fold', 'indent'],
      highlight: 'full',
      defaultViewMode: 'edit',
      readOnly: false,
      description: 'properties：按 §5.3 口径用「text + 高亮」，无对应语言包时退化为纯文本',
    },
  },
  {
    exts: ['css'],
    detail: {
      group: 'css',
      capabilities: ['fold', 'indent'],
      highlight: 'full',
      defaultViewMode: 'edit',
      readOnly: false,
      description: 'CSS：折叠 / 缩进',
    },
  },
  {
    exts: ['py', 'js', 'ts', 'tsx', 'jsx', 'sh', 'bat', 'ps1', 'java', 'go', 'rs', 'c', 'cpp'],
    detail: {
      group: 'code',
      capabilities: ['bracketMatch', 'autoIndent', 'commentToggle'],
      highlight: 'full',
      defaultViewMode: 'edit',
      readOnly: false,
      description: '代码类：括号匹配 / 自动缩进 / 行注释切换（各扩展名对应各自语言包）',
    },
  },
  {
    exts: ['log', 'txt'],
    detail: {
      group: 'plain',
      capabilities: ['monospace', 'wordWrap', 'largeFileGuard'],
      highlight: 'plaintext',
      defaultViewMode: 'edit',
      readOnly: false,
      description: '纯文本：等宽字体 / 自动换行 / 大文件保护（§5.2 的阈值降级）',
    },
  },
  {
    exts: ['diff', 'patch'],
    detail: {
      group: 'diff',
      capabilities: [],
      highlight: 'full',
      defaultViewMode: 'edit',
      readOnly: true,
      description: 'Diff / Patch：强制只读预览（readOnly=true），保存入口禁用',
    },
  },
];

/** §5.3 第 10 行：未知扩展名 + 文本内容 → 按 txt 处理，可手动切语言；**不进关联清单** */
export const UNKNOWN_TEXT_TYPE: ScratchExtInfo = {
  ext: '',
  label: '未知扩展名（文本）',
  language: 'text',
  mime: [],
  group: 'unknown-text',
  capabilities: ['monospace', 'wordWrap', 'largeFileGuard'],
  highlight: 'plaintext',
  defaultViewMode: 'edit',
  readOnly: false,
  description: '未知扩展名但内容判定为文本：按 txt 处理，可手动切换语言（不进文件关联清单）',
};

/** §5.3 第 8 行的「无扩展名」分支：按 txt 处理，但**没有扩展名可注册**，故不进清单 */
export const NO_EXTENSION_TEXT_TYPE: ScratchExtInfo = {
  ext: '',
  label: '无扩展名文本',
  language: 'text',
  mime: [],
  group: 'plain-no-ext',
  capabilities: ['monospace', 'wordWrap', 'largeFileGuard'],
  highlight: 'plaintext',
  defaultViewMode: 'edit',
  readOnly: false,
  description: '无扩展名的文本文件：按 txt 处理（不进文件关联清单）',
};

/**
 * §5.3 第 11 行：未知扩展名 + 二进制内容 → **不加载、不产生窗格**（AC-50）。
 *
 * 判定在 Python 侧 `text_detect_service`；渲染层只消费 `ReadItem.ok:false, reason:"binary"`，
 * 这里把"拒绝策略"固化成机器可读常量，避免 UI 各写一份判断。
 */
export const BINARY_REJECT_POLICY = {
  loadable: false,
  paneCreated: false,
  reason: 'binary',
  description: '未知扩展名且内容判定为二进制：不加载、不产生窗格，由 editor.notice 提示',
} as const;

/** 能力组摊平后的查表（`ext` → 能力组） */
const DETAIL_BY_EXT: Map<string, TypeDetail> = (() => {
  const map = new Map<string, TypeDetail>();
  TYPE_DETAIL_GROUPS.forEach(({ exts, detail }) => {
    exts.forEach((ext) => map.set(ext, detail));
  });
  return map;
})();

/** 未在能力组表登记的 ext 的保守兜底（视为纯文本；正常不应发生） */
const FALLBACK_DETAIL: TypeDetail = {
  group: 'plain',
  capabilities: ['monospace', 'wordWrap', 'largeFileGuard'],
  highlight: 'plaintext',
  defaultViewMode: 'edit',
  readOnly: false,
  description: '未登记能力组的扩展名：按纯文本处理',
};

/**
 * §5.1 点名的派生量①：**每个扩展名一条**的完整类型信息
 * （= manifest 字段 + 能力 / 高亮 / 默认视图 / 只读标记 + 表格行 id）。
 */
export const EXTENSIONS: ScratchExtInfo[] = TYPES.map((entry) => {
  const detail = DETAIL_BY_EXT.get(entry.ext) ?? FALLBACK_DETAIL;
  return {
    ext: entry.ext,
    label: entry.label,
    language: entry.language,
    mime: [...entry.mime],
    group: detail.group,
    capabilities: [...detail.capabilities],
    highlight: detail.highlight,
    defaultViewMode: detail.defaultViewMode,
    readOnly: detail.readOnly,
    description: detail.description,
  };
});

/** 关联清单条目 = 有明确扩展名的条目（§5.1 判据：TYPES 与清单逐项一致） */
export const MANIFEST_ENTRIES: TypeEntry[] = TYPES;

/** 关联清单里的扩展名（小写、无点），供设置页与 Python 侧比对 */
export const ASSOC_EXTENSIONS: string[] = TYPES.map((entry) => entry.ext);

/** §5.1 点名的派生量②：全部语言 id（按首次出现顺序去重） */
export const LANGUAGES: ScratchLanguage[] = Array.from(
  new Set(TYPES.map((entry) => entry.language)),
);

/** §5.1 点名的派生量③：全部能力 id（按首次出现顺序去重，用于能力表渲染与校验） */
export const CAPABILITIES: ScratchCapability[] = Array.from(
  new Set(EXTENSIONS.flatMap((entry) => entry.capabilities)),
);

/* ────────────────────────────────────────────────────────────────────────────
 * 显示名（界面文案）：**不参与** manifest 文本派生（派生只切 `TYPES` 字面量）。
 *
 * 证据：`scripts/gen_file_assoc_manifest.mjs` 只取 `export const TYPES` 之后**第一个** `[`
 * 到配平 `]`，本段全部在它之外 ⇒ 改这里**不会**让 `--check` 变红（设计 §15.2.5 已核）。
 * ──────────────────────────────────────────────────────────────────────────── */

/**
 * 语言 id → 中文显示名（**唯一真相**，AC-74⑦）。
 *
 * ⚠ `text` 一档**故意不区分** csv / tsv —— 它由 `languageLabel(language, ext)` 的 `ext`
 * 参数覆盖（`.txt` ⇒「纯文本 (txt)」、`.csv` ⇒「CSV」、`.tsv` ⇒「TSV」）。
 * 覆盖集必须含 `ScratchLanguage` 的全部 20 个取值（新增语言时 tsc 会报缺键）。
 */
export const LANGUAGE_LABELS: Readonly<Record<ScratchLanguage, string>> = Object.freeze({
  json: 'JSON',
  markdown: 'Markdown',
  text: '纯文本',
  sql: 'SQL',
  xml: 'XML',
  html: 'HTML',
  yaml: 'YAML',
  css: 'CSS',
  python: 'Python',
  javascript: 'JavaScript',
  typescript: 'TypeScript',
  shell: 'Shell 脚本',
  batch: '批处理',
  powershell: 'PowerShell 脚本',
  java: 'Java',
  go: 'Go',
  rust: 'Rust',
  c: 'C',
  cpp: 'C++',
  diff: '差异对比',
});

/**
 * 语言显示名（**ext 优先**，AC-74⑦ / 台账 B1 的误标修正）。
 *
 * 口径（三条，逐条对齐 AC-74⑦ 的判据）：
 *   ① `language === 'text'` **且**给了 `ext` ⇒ 用类型表里该 ext 的 label
 *      （`.txt` ⇒「纯文本 (txt)」、`.csv` ⇒「CSV」、`.tsv` ⇒「TSV」）⇒ 三者互不相同；
 *   ② 其它 language ⇒ 用 `LANGUAGE_LABELS[language]`（与扩展名无关，`.py` 恒「Python」）；
 *   ③ 两者都拿不到 ⇒ 回退 `language` 原文（**不编造**，与改造前的 `ScratchToolbar` 口径一致）。
 *
 * ⚠ **现状缺陷（改造前实测）**：只按 `language` 取「首个匹配 TYPES 项的 label」时，
 * `csv` / `tsv` 都是 `language:'text'` ⇒ `text` 一档的**全部 8 个扩展名**
 * （`txt` / `csv` / `tsv` / `log` / `toml` / `ini` / `conf` / `properties`）都显示「CSV」，
 * 且 `.csv` 与 `.tsv` 无法区分。本函数按 ext 覆盖即修掉这条。
 */
export function languageLabel(language: string, ext?: string | null): string {
  if (language === 'text' && ext) {
    const normalized = normalizeExtension(ext);
    const entry =
      EXT_INFO_BY_EXT.get(normalized) ?? EXTENSIONS.find((item) => item.ext === normalized);
    if (entry) return entry.label;
  }
  return LANGUAGE_LABELS[language as ScratchLanguage] ?? language;
}

/**
 * SQL 方言 id → **专有名词白名单**（AC-74⑧ 允许业界通名 / 专有名词不计入失败）。
 *
 * 用**可判定白名单**表达"专有名词"，而不是主观判断：命中本集合的 id **保留原名**
 * （若 `DIALECT_CN` 有业界固定大小写则用固定写法），但仍按 AC-74⑥ 拼上 `（id）`
 * （该条要求"0 项的可见文本等于其内部英文 id 字面"）
 * ⇒ `PostgreSQL（postgresql）`、`MySQL（mysql）`；未命中的走中文名（`Oracle PL/SQL（plsql）`）。
 *
 * ⚠ 覆盖面**必须**等于运行时 `api.formatters.sqlDialects` 全集（离线包实测 **21** 项，
 * 见开发记录）：未覆盖的 id 只能在 `DIALECT_CN` 里逐项登记，否则 `sqlDialectLabel()`
 * 会裸回退成英文 id ⇒ AC-74⑥ 失败。
 */
export const PROPER_NOUN_DIALECTS: ReadonlySet<string> = new Set([
  'bigquery',
  'clickhouse',
  'duckdb',
  'db2',
  'db2i',
  'hive',
  'mariadb',
  'mysql',
  'n1ql',
  'plsql',
  'postgresql',
  'redshift',
  'singlestoredb',
  'snowflake',
  'spark',
  'sql',
  'sqlite',
  'tidb',
  'transactsql',
  'trino',
  'tsql',
]);

/**
 * 需要**明确显示名**的方言（**必须覆盖**运行时 `formatters.sqlDialects` 全集）。
 *
 * ⚠ 取值空间由运行时全集钉死（离线包实测 **21** 项）：**每一项都必须在这里有着落**，
 * 否则 `sqlDialectLabel()` 会回退成 id 原文 —— 那是"看起来配了、实际没本地化"的静默失败，
 * 由 `__tests__/scratch-type-labels.test.ts` 的"全集遍历"断言拦住。
 *
 * 两种写法（`PROPER_NOUN_DIALECTS` 决定"是否专有名词"，本表给"显示什么"）：
 *   - 专有名词 ⇒ 固定大小写（`MySQL（mysql）`、`PostgreSQL（postgresql）`）；
 *   - 非专有名词 ⇒ 中文名 + `（id）`（`Oracle PL/SQL（plsql）`、`T-SQL（tsql）`）；
 *   - 专有名词但无业界固定大小写（如 `sqlite`）⇒ 走"id 原名 + （id）"分支。
 */
const DIALECT_CN: Readonly<Record<string, string>> = Object.freeze({
  // 专有名词（`PROPER_NOUN_DIALECTS` 成员；本表给固定大小写）
  bigquery: 'BigQuery',
  clickhouse: 'ClickHouse',
  duckdb: 'DuckDB',
  db2: 'DB2',
  db2i: 'DB2i',
  hive: 'Hive',
  mariadb: 'MariaDB',
  mysql: 'MySQL',
  n1ql: 'N1QL',
  postgresql: 'PostgreSQL',
  redshift: 'Redshift',
  singlestoredb: 'SingleStoreDB',
  snowflake: 'Snowflake',
  spark: 'Spark',
  sql: '标准 SQL',
  sqlite: 'SQLite',
  tidb: 'TiDB',
  transactsql: 'SQL Server（transactsql）',
  trino: 'Trino',
  // 名字是专有名词、但**中文文案里惯用中文描述**的两项（设计 §15.2.7 的 `DIALECT_CN` 原样）
  plsql: 'Oracle 过程语言',
  tsql: 'T-SQL 方言',
});

/**
 * 方言显示名（AC-74⑥）。
 *
 * 写法 = 「中文名 / 专有名词 + `（id）`」：
 *   - `DIALECT_CN` 有显示名 ⇒ `${显示名}（${id}）`（含 `MySQL（mysql）`、`Oracle PL/SQL（plsql）`）；
 *   - 是专有名词但表里没给显示名 ⇒ `${id}（${id}）`（**不编造**，但仍满足"不得等于 id"）；
 *   - 完全未知的 id ⇒ **回退 id 原文** —— 该分支会让 AC-74⑥ 的"全集遍历"失败，
 *     是刻意的信号（新增方言必须登记），不是兜底。
 *
 * 为什么**所有**分支都拼 `（id）`：AC-74⑥ 与 ⑧ 存在张力 —— ⑧ 说专有名词"未命中不算失败"，
 * ⑥ 说"0 项等于 id 字面" ⇒ 同时满足的唯一写法就是"原名 + （id）"。`value` 仍是原始 id，
 * 格式化行为零变化（`toSqlFormatOptions` 逐字不变）。
 */
export function sqlDialectLabel(id: string): string {
  const key = id.trim().toLowerCase();
  const cn = DIALECT_CN[key];
  if (cn) return `${cn}（${id}）`;
  if (PROPER_NOUN_DIALECTS.has(key)) return `${id}（${id}）`;
  return id;
}

const EXT_INFO_BY_EXT: Map<string, ScratchExtInfo> = new Map(
  EXTENSIONS.map((entry) => [entry.ext, entry]),
);

/** 归一化扩展名：去点、去空白、转小写（`"A.JSON"` → `"json"`，无扩展名 → `""`） */
export function normalizeExtension(raw: string | null | undefined): string {
  if (!raw) return '';
  return raw.trim().replace(/^\.+/, '').toLowerCase();
}

/** 从路径里取扩展名（只取扩展名，**不保留也不打印路径**；§9.14） */
export function getExtFromPath(path: string): string {
  const name = path.split(/[\\/]/).pop() ?? '';
  const dot = name.lastIndexOf('.');
  if (dot <= 0) return '';
  return normalizeExtension(name.slice(dot + 1));
}

/** 该扩展名是否进关联清单（清单外的扩展名仍可被用户在系统层面手动绑定，见 §5.3 注） */
export function isAssocExtension(ext: string): boolean {
  return EXT_INFO_BY_EXT.has(normalizeExtension(ext));
}

/** 按扩展名取类型信息；未知 / 空扩展名返回 `null`（调用方用 `resolveTypeForExt` 拿兜底条目） */
export function getExtInfo(ext: string): ScratchExtInfo | null {
  return EXT_INFO_BY_EXT.get(normalizeExtension(ext)) ?? null;
}

/**
 * 解析扩展名 → 类型信息（**永不返回 null**）：
 * 已知扩展名 → 表内条目；无扩展名 → `NO_EXTENSION_TEXT_TYPE`；其它 → `UNKNOWN_TEXT_TYPE`。
 *
 * 二进制内容的分支不在这里 —— 它由 `ReadItem.ok:false, reason:"binary"` 表达（`BINARY_REJECT_POLICY`）。
 */
export function resolveTypeForExt(ext: string): ScratchExtInfo {
  const normalized = normalizeExtension(ext);
  if (normalized === '') return NO_EXTENSION_TEXT_TYPE;
  return EXT_INFO_BY_EXT.get(normalized) ?? UNKNOWN_TEXT_TYPE;
}

/** 解析路径 → 类型信息（内部只截扩展名，不保留路径） */
export function resolveTypeForPath(path: string): ScratchExtInfo {
  return resolveTypeForExt(getExtFromPath(path));
}

/** 判断类型是否具备某项能力 */
export function hasCapability(info: ScratchExtInfo, capability: ScratchCapability): boolean {
  return info.capabilities.includes(capability);
}

/**
 * 手动切换语言后，取该语言的**能力并集**（同语言可能有多个扩展名）。
 *
 * 用途：`scratch-store.setLanguage` 校验视图模式是否仍可用（F4.2）。
 * 语言不在表内（理论不可达）时返回纯文本能力。
 */
export function getCapabilitiesForLanguage(language: string): ScratchCapability[] {
  const caps = new Set<ScratchCapability>();
  EXTENSIONS.forEach((entry) => {
    if (entry.language === language) entry.capabilities.forEach((cap) => caps.add(cap));
  });
  if (caps.size === 0) return ['monospace', 'wordWrap', 'largeFileGuard'];
  return Array.from(caps);
}

/** 手动切换语言后的默认视图模式（取该语言首个条目的默认视图） */
export function getDefaultViewModeForLanguage(language: string): ScratchViewMode {
  const hit = EXTENSIONS.find((entry) => entry.language === language);
  return hit?.defaultViewMode ?? 'edit';
}

/** §5.1 的 manifest 结构（`toFileAssocManifest()` 的返回形状） */
export interface FileAssocManifest {
  version: number;
  generatedFrom: string;
  types: TypeEntry[];
}

/** manifest 版本（§5.1 固定为 1） */
export const MANIFEST_VERSION = 1;

/** manifest 的来源标注（生成物里写死，便于核对派生链） */
export const MANIFEST_GENERATED_FROM = 'config/scratch-types.ts';

/**
 * 派生 §5.1 的 manifest 内容（与 `gen_file_assoc_manifest.mjs` 的文本派生结果**必须一致**，
 * 判据：`python jflove-desktop/scripts/check_file_assoc_manifest.py --check` 退出码 0）。
 */
export function toFileAssocManifest(): FileAssocManifest {
  return {
    version: MANIFEST_VERSION,
    generatedFrom: MANIFEST_GENERATED_FROM,
    types: MANIFEST_ENTRIES.map((entry) => ({ ...entry, mime: [...entry.mime] })),
  };
}
