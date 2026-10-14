/**
 * R-6 实测：xml-formatter@3 与 sql-formatter@15 的行为核对
 *
 * 为 v1.6.0 桌面端「文本编辑器」工具箱第 9/10 项（SQL 美化 / XML 格式化·压缩）定稿提供证据。
 * 不修改任何项目源码；依赖装在系统临时目录（npm install --no-save）。
 *
 * 运行：
 *   node jflove-desktop/poc/editor-probes/r6-format-libs.mjs
 * 可用环境变量 FMT_PROBE_DIR 指定临时工程目录（默认 %TEMP%\jflove-fmt-probe）。
 *
 * 产出：
 *   r6-out/<用例名>.in.<ext> / .out.<ext>   —— 逐用例的输入与输出完整原文
 *   r6-results.json                          —— 全部用例的结构化结果
 *   r6-report.md                             —— 人读的逐用例表
 */
import { createRequire } from 'node:module';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT_DIR = path.join(__dirname, 'r6-out');
const PROBE_DIR = process.env.FMT_PROBE_DIR || path.join(os.tmpdir(), 'jflove-fmt-probe');
const require = createRequire(path.join(PROBE_DIR, '__probe_require__.cjs'));

fs.mkdirSync(OUT_DIR, { recursive: true });

const xmlFormat = require('xml-formatter');
const sqlMod = require('sql-formatter');
const sqlFormat = sqlMod.format || sqlMod.default;

const versions = {
  'xml-formatter': require('xml-formatter/package.json').version,
  'sql-formatter': require('sql-formatter/package.json').version,
  node: process.version,
};

const cases = [];
const results = [];

/** 收集一个用例：写 .in/.out 原始文件并记录结果 */
function record({ id, group, title, ext, input, output, error, extra = {} }) {
  const base = `${id}`;
  const inFile = path.join(OUT_DIR, `${base}.in.${ext}`);
  const outFile = path.join(OUT_DIR, `${base}.out.${ext}`);
  fs.writeFileSync(inFile, input, 'utf8');
  if (typeof output === 'string') {
    fs.writeFileSync(outFile, output, 'utf8');
  }
  const row = {
    id,
    group,
    title,
    in_file: path.relative(__dirname, inFile),
    out_file: typeof output === 'string' ? path.relative(__dirname, outFile) : null,
    input,
    output: typeof output === 'string' ? output : null,
    error: error ? { name: error.name, message: error.message } : null,
    ...extra,
  };
  results.push(row);
  console.log(
    `[${id}] ${title}\n    错误: ${row.error ? row.error.name + ': ' + row.error.message : '无'}`
  );
  return row;
}

/** 安全执行：把异常也作为"实测结果"记录下来，而不是让脚本中断 */
function attempt(fn) {
  try {
    return { value: fn(), error: null };
  } catch (e) {
    return { value: null, error: e };
  }
}

function show(s) {
  return s === null || s === undefined ? String(s) : JSON.stringify(s);
}

// ==========================================================================
// XML 用例
// ==========================================================================
const XML_OPTS_DEFAULT = {}; // 全部走库默认值，先看"默认行为"
const XML_OPTS_RECOMMENDED = { indentation: '  ', lineSeparator: '\n', attributeQuotes: 'double' };

function xmlCase(id, title, input, opts = XML_OPTS_DEFAULT, minify = false, extra = {}) {
  const fn = minify ? xmlFormat.minify : xmlFormat;
  const { value, error } = attempt(() => fn(input, opts));
  return record({ id, group: 'XML', title, ext: 'xml', input, output: value, error, extra });
}

// X1 注释是否保留
xmlCase(
  'x1-comment',
  '含注释 <!-- ... -->：格式化后是否保留',
  '<root><!--keep-me--><a>1</a><!-- second comment --><b>2</b></root>'
);
// X2 CDATA 是否保留、内部空白是否被改动
xmlCase(
  'x2-cdata',
  'CDATA：是否保留 + CDATA 内空白是否被改动',
  '<root><a><![CDATA[  x < y\n   \t spaced  ]]></a></root>'
);
// X2b AC-33 的原始输入样例（需求文档 AC-33 原文）
xmlCase(
  'x2b-ac33-sample',
  'AC-33 原文样例：<root><!--c--><a><![CDATA[ x < y ]]></a></root>',
  '<root><!--c--><a><![CDATA[ x < y ]]></a></root>'
);
// X3 自闭合标签
xmlCase('x3-selfclosing', '自闭合标签 <br/> 与 <img src="x" /> 是否变成 <br></br>', '<r><br/><img src="x" /><hr /></r>');
// X3b 空元素（非自闭合写法）
xmlCase('x3b-empty-tag', '空元素 <a></a> 是否被改成自闭合', '<r><a></a><b> </b><c/></r>');
// X4 属性引号风格：单引号
xmlCase('x4-single-quote', "单引号属性 attr='v' 是否被改写", "<r a='1' b='two words'><c d='3'/></r>");
// X4b 属性引号风格：无引号（非法 XML）
xmlCase('x4b-unquoted-attr', '无引号属性值 b=2（非法 XML）是否报错 / 被改写', '<r a=2 b=x><c/></r>');
// X4c 属性值里含实体与单引号
xmlCase('x4c-attr-entity', '属性含实体 &amp; 与内嵌引号', '<r a="x&amp;y" b=\'it"quote\'/>');
// X5 文本节点内空白
xmlCase('x5-text-whitespace', '文本节点内空白 <a>hello   world</a> 是否被压缩', '<r><a>hello   world</a><b>  lead and trail  </b></r>');
// X5b xml:space="preserve"
xmlCase(
  'x5b-xml-space',
  'xml:space="preserve" 是否被尊重（文档宣称支持）',
  '<r><p xml:space="preserve">This is <b>some</b> content.</p></r>'
);
// X5c collapseContent
xmlCase(
  'x5c-collapse-content',
  'collapseContent:true 对文本节点空白的影响',
  '<r><a>hello   world</a><b>  lead  </b></r>',
  { collapseContent: true, lineSeparator: '\n', indentation: '  ' }
);
// X6 压缩模式：参数怎么传 + 有意义的一个空格文本节点会不会被吃
xmlCase('x6-minify-basic', '压缩模式 xmlFormat.minify()：元素间空白', '<r>\n  <a>1</a>\n  <b>2</b>\n</r>', {}, true);
xmlCase(
  'x6b-minify-space-textnode',
  '压缩：<a> </a>（有意义的一个空格文本节点）是否被吃成 <a></a>',
  '<r>\n  <a> </a>\n  <b>x</b>\n</r>',
  {},
  true
);
xmlCase(
  'x6c-minify-textnode-inner',
  '压缩：文本节点内部空白 "hello   world"（非元素间）是否被改',
  '<r>\n  <a>hello   world</a>\n</r>',
  {},
  true
);
xmlCase(
  'x6d-minify-collapse',
  '压缩 + collapseContent:true（README 示例看到 "some<b>content" 空格消失）',
  '<r><p>This is <b>some</b> content.</p></r>',
  { collapseContent: true },
  true
);
xmlCase(
  'x6e-minify-ac33',
  'AC-33 要求：压缩后仍可被标准 XML 解析器解析，且文本节点内容不变',
  '<root><!--c--><a><![CDATA[ x < y ]]></a><b>hello   world</b></root>',
  {},
  true
);
// X7 非法 XML
xmlCase('x7-invalid-missing-close', '非法 XML（缺少闭合标签）是否抛异常', '<root><a>1</root>');
xmlCase(
  'x7b-invalid-throwonfailure-false',
  '非法 XML + throwOnFailure:false → 是否静默返回原文',
  '<root><a>1</root>',
  { throwOnFailure: false }
);
xmlCase('x7c-invalid-strict', '非法 XML + strictMode:true', '<root><a>1</root>', { strictMode: true });
xmlCase('x7d-plain-text', '完全不是 XML 的纯文本（应报错）', 'hello world\nnot xml at all');
// X8 声明与 BOM
xmlCase(
  'x8-declaration',
  '声明 <?xml version="1.0" encoding="UTF-8"?> 是否保留',
  '<?xml version="1.0" encoding="UTF-8"?>\n<r><a>1</a></r>'
);
xmlCase('x8b-bom', 'BOM（\\uFEFF）是否保留、是否重复', '\uFEFF<?xml version="1.0" encoding="UTF-8"?><r><a>1</a></r>');
xmlCase(
  'x8c-no-decl',
  '无声明时是否被加上声明',
  '<r><a>1</a></r>'
);
// X9 推荐参数下的回归（用于给"推荐调用参数"背书）
xmlCase(
  'x9-recommended',
  '推荐参数 {indentation:"  ", lineSeparator:"\\n", attributeQuotes:"double"} 下的格式化',
  '<?xml version="1.0" encoding="UTF-8"?>\n<root><!--c--><a k=\'v\'><![CDATA[ x < y ]]></a><br/><b>hello   world</b></root>',
  XML_OPTS_RECOMMENDED
);
// X10 CRLF 默认行分隔符的影响
xmlCase(
  'x10-default-linesep',
  '默认 lineSeparator 是否为 \\r\\n（跨平台一致性）',
  '<r><a>1</a><b>2</b></r>'
);

// ==========================================================================
// SQL 用例
// ==========================================================================
const SQL_LANGS = (() => {
  const dir = path.join(PROBE_DIR, 'node_modules', 'sql-formatter', 'dist', 'cjs', 'languages');
  try {
    return fs
      .readdirSync(dir, { withFileTypes: true })
      .filter((d) => d.isDirectory())
      .map((d) => d.name)
      .sort();
  } catch {
    return [];
  }
})();

function sqlCase(id, title, input, opts) {
  const { value, error } = attempt(() => sqlFormat(input, opts));
  return record({ id, group: 'SQL', title, ext: 'sql', input, output: value, error, extra: { opts } });
}

const SAMPLE_SQL = 'select id, name from users where age > 18 and city = \'Beijing\' order by id;';

sqlCase('s2a-keywordCase-upper', "keywordCase:'upper'", SAMPLE_SQL, { language: 'sql', keywordCase: 'upper', tabWidth: 2 });
sqlCase('s2b-keywordCase-lower', "keywordCase:'lower'", 'SELECT ID FROM USERS WHERE X = 1;', { language: 'sql', keywordCase: 'lower', tabWidth: 2 });
sqlCase('s2c-keywordCase-preserve', "keywordCase:'preserve'", SAMPLE_SQL, { language: 'sql', keywordCase: 'preserve', tabWidth: 2 });
sqlCase(
  's2d-case-scope',
  'keywordCase 是否误改标识符 / 字符串字面量 / 函数名',
  "select Foo, 'Select Bar' as Lit, count(*) from MyTable where name = 'upper CASE';",
  { language: 'sql', keywordCase: 'upper', tabWidth: 2 }
);
sqlCase(
  's2e-identifierCase',
  'identifierCase / dataTypeCase / functionCase 的独立效果',
  "select Foo, count(*) from MyTable where x = 'y';",
  { language: 'sql', keywordCase: 'upper', identifierCase: 'lower', dataTypeCase: 'upper', functionCase: 'lower', tabWidth: 2 }
);

const COMMENT_SQL = [
  '-- 顶部行注释',
  'select a, -- 行内注释',
  '       /* 块注释 */ b',
  'from t /* 跨',
  '   行块注释 */ where a = 1; -- 末尾行注释',
].join('\n');
sqlCase('s3-comments', '行注释 / 块注释 / 行内注释是否保留与位置漂移', COMMENT_SQL, { language: 'sql', keywordCase: 'upper', tabWidth: 2 });
sqlCase(
  's3b-comment-hint',
  '优化器提示 /*+ ... */ 在 select 之后的位置',
  'select /*+ INDEX(t idx1) */ a from t where a = 1;',
  { language: 'sql', keywordCase: 'upper', tabWidth: 2 }
);
sqlCase(
  's3c-comment-only',
  '仅含注释的文本（无语句）→ 是否报错',
  '-- just a comment\n/* and another */',
  { language: 'sql' }
);

sqlCase('s4a-tabWidth-2', 'tabWidth:2', SAMPLE_SQL, { language: 'sql', tabWidth: 2 });
sqlCase('s4b-tabWidth-8', 'tabWidth:8', SAMPLE_SQL, { language: 'sql', tabWidth: 8 });
sqlCase('s4c-useTabs', 'useTabs:true 的实际字符（应是 \\t）', SAMPLE_SQL, { language: 'sql', tabWidth: 4, useTabs: true });
sqlCase(
  's4d-indentStyle',
  "indentStyle:'tabularLeft' 与 'standard' 的差异",
  'select a, b, c from t where a = 1 and b = 2;',
  { language: 'sql', tabWidth: 2, indentStyle: 'tabularLeft' }
);
sqlCase(
  's4e-linesBetweenQueries',
  'linesBetweenQueries 对多语句间距的影响',
  'select 1; select 2;',
  { language: 'sql', linesBetweenQueries: 3, tabWidth: 2 }
);

sqlCase('s5a-string-semicolon', "字符串里的分号 SELECT 'a;b' AS c FROM t", "SELECT 'a;b' AS c FROM t;", { language: 'sql', tabWidth: 2 });
sqlCase(
  's5b-string-keyword',
  '字符串里的关键字与注释形文本是否被误解析',
  "SELECT 'select -- not a comment /* neither */ ; from' AS c FROM t;",
  { language: 'sql', tabWidth: 2 }
);
sqlCase(
  's5c-quoted-identifier',
  '双引号标识符 / 反引号标识符里的关键字',
  'SELECT "from" FROM `where` WHERE x = \'and\';',
  { language: 'sql', tabWidth: 2 }
);
sqlCase(
  's5d-dollar-quote',
  'PostgreSQL 美元引用 $$ ... $$ 里的分号与关键字',
  "SELECT $$a;b select$$ AS c FROM t;",
  { language: 'postgresql', tabWidth: 2 }
);
sqlCase('s5e-params', '参数占位符 :name / @p / ? 是否被改写', 'select * from t where a = :name and b = @p and c = ?;', { language: 'sql', tabWidth: 2 });

const MULTI_SQL = [
  '-- 存储过程示例（PostgreSQL）',
  'CREATE OR REPLACE FUNCTION add_one(x int) RETURNS int AS $$',
  'BEGIN',
  '  RETURN x + 1;',
  'END;',
  '$$ LANGUAGE plpgsql;',
  'INSERT INTO t (a) VALUES (1);',
  'UPDATE t SET a = a + 1;',
].join('\n');
sqlCase('s6a-multi-statement', '多语句（; 分隔）能否处理', 'select 1;\nselect 2;\ndelete from t where a=1;', { language: 'sql', tabWidth: 2 });
sqlCase('s6b-stored-proc-pg', 'PostgreSQL 存储过程（含 $$ 体与内部分号）', MULTI_SQL, { language: 'postgresql', tabWidth: 2 });
sqlCase(
  's6c-stored-proc-mysql',
  'MySQL 存储过程（DELIMITER 与 BEGIN...END）',
  'DELIMITER //\nCREATE PROCEDURE p()\nBEGIN\n  SELECT 1;\nEND //\nDELIMITER ;',
  { language: 'mysql', tabWidth: 2 }
);
sqlCase(
  's6d-transactsql',
  'T-SQL 的 GO 批处理分隔符',
  'SELECT 1\nGO\nSELECT 2\nGO',
  { language: 'transactsql', tabWidth: 2 }
);
sqlCase('s6e-missing-semicolon', '缺少结尾分号的单条语句', 'select a from t where b = 1', { language: 'sql', tabWidth: 2 });

sqlCase('s7a-invalid-truncated', '非法 SQL：截断的语句 select from where', 'select from where;', { language: 'sql', tabWidth: 2 });
sqlCase('s7b-invalid-unbalanced', '非法 SQL：未闭合括号 / 引号', "select (a from t where b = 'unclosed;", { language: 'sql', tabWidth: 2 });
sqlCase('s7c-not-sql', '完全不是 SQL 的纯文本（XML 片段）', '<root><a>1</a></root>', { language: 'sql', tabWidth: 2 });
sqlCase('s7d-plain-words', '纯文字（中文散文）→ 是否抛错还是原样返回', '这是一段中文，不是 SQL 语句。', { language: 'sql', tabWidth: 2 });
sqlCase('s7e-bad-option', '非法语言取值 / 非法 keywordCase → 是否明确抛错', 'select 1;', { language: 'not-a-dialect', tabWidth: 2 });
sqlCase('s7f-bad-keywordcase', '非法 keywordCase 取值', 'select 1;', { language: 'sql', keywordCase: 'shout' });

// 方言覆盖：同一复杂语句在 20 个方言下是否都成功
const DIALECT_SQL = 'select a, b from t where a = 1 order by b limit 10;';
const dialectRuns = SQL_LANGS.map((lng) => {
  const { value, error } = attempt(() => sqlFormat(DIALECT_SQL, { language: lng, tabWidth: 2, keywordCase: 'upper' }));
  return { language: lng, ok: !error, error: error ? `${error.name}: ${error.message}` : null, output: value };
});
record({
  id: 's1-dialects',
  group: 'SQL',
  title: `方言枚举与 20 个方言的实际可用性（共 ${SQL_LANGS.length} 个）`,
  ext: 'json',
  input: DIALECT_SQL,
  output: JSON.stringify({ languages: SQL_LANGS, runs: dialectRuns }, null, 2),
  error: null,
  extra: { languages: SQL_LANGS, dialect_runs: dialectRuns },
});

// ==========================================================================
// 汇总输出
// ==========================================================================
const payload = {
  versions,
  probe_dir: PROBE_DIR,
  sql_languages: SQL_LANGS,
  results,
};
fs.writeFileSync(path.join(__dirname, 'r6-results.json'), JSON.stringify(payload, null, 2), 'utf8');

const md = [
  `# R-6 实测原始输出（xml-formatter ${versions['xml-formatter']} / sql-formatter ${versions['sql-formatter']} / Node ${versions.node}）`,
  '',
  '每个用例的完整输入输出原文见 `r6-out/<用例名>.in.*` 与 `.out.*`。',
  '',
  '| 用例 | 组 | 标题 | 抛错 | 输入 | 输出 |',
  '|---|---|---|---|---|---|',
];
for (const r of results) {
  const q = (s) => (s === null || s === undefined ? '(无)' : '`' + String(s).replace(/\|/g, '\\|').replace(/\n/g, '\\n').slice(0, 220) + '`');
  md.push(
    `| ${r.id} | ${r.group} | ${r.title} | ${r.error ? r.error.name : '—'} | ${q(r.input)} | ${q(r.output)} |`
  );
}
if (SQL_LANGS.length) {
  md.push('', '## SQL 方言枚举（来自 node_modules/sql-formatter/dist/cjs/languages/）', '', '```', SQL_LANGS.join(', '), '```', '');
  md.push('| 方言 | 复杂语句是否成功 | 失败原因 |', '|---|---|---|');
  for (const d of dialectRuns) md.push(`| ${d.language} | ${d.ok ? '✅' : '❌'} | ${d.error || '—'} |`);
}
fs.writeFileSync(path.join(__dirname, 'r6-report.md'), md.join('\n'), 'utf8');

console.log('\n=== 版本 ===');
console.log(show(versions));
console.log(`SQL 方言（${SQL_LANGS.length}）：${SQL_LANGS.join(', ')}`);
console.log(`\n已写出：${path.join(__dirname, 'r6-results.json')}`);
console.log(`已写出：${path.join(__dirname, 'r6-report.md')}`);
console.log(`原始输入输出：${OUT_DIR}`);
