/**
 * R-6 附加用例：超长 / 超深嵌套下的行为（是否卡死 / 爆栈）
 *
 * 与主脚本分开，是因为"卡死"必须靠外部超时兜住 —— 单独跑它，卡住也只损失这一条。
 *
 * 运行：
 *   node jflove-desktop/poc/editor-probes/r6-deep-nesting.mjs [深度]
 * 默认依次跑 SQL 200 层括号 / 1000 层括号 / XML 200 层嵌套 / XML 5000 层嵌套。
 */
import { createRequire } from 'node:module';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROBE_DIR = process.env.FMT_PROBE_DIR || path.join(os.tmpdir(), 'jflove-fmt-probe');
const require = createRequire(path.join(PROBE_DIR, '__probe_require__.cjs'));
const xmlFormat = require('xml-formatter');
const sqlFormat = require('sql-formatter').format;

const OUT = path.join(__dirname, 'r6-deep-nesting.json');
const rows = fs.existsSync(OUT) ? JSON.parse(fs.readFileSync(OUT, 'utf8')).rows || [] : [];

function run(id, title, fn) {
  const t0 = Date.now();
  let out = null;
  let err = null;
  try {
    out = fn();
  } catch (e) {
    err = `${e.name}: ${e.message}`;
  }
  const ms = Date.now() - t0;
  const row = {
    id,
    title,
    ms,
    ok: !err,
    error: err,
    output_len: typeof out === 'string' ? out.length : null,
    output_head: typeof out === 'string' ? out.slice(0, 120) : null,
  };
  rows.push(row);
  console.log(JSON.stringify(row, null, 2));
  return row;
}

// SQL：n 层括号
function sqlParens(n) {
  return `select ${'('.repeat(n)}1${')'.repeat(n)} as x from t;`;
}

run('d1-sql-parens-50', 'SQL 50 层括号', () => sqlFormat(sqlParens(50), { language: 'sql', tabWidth: 2 }));
run('d2-sql-parens-200', 'SQL 200 层括号', () => sqlFormat(sqlParens(200), { language: 'sql', tabWidth: 2 }));
run('d3-sql-parens-1000', 'SQL 1000 层括号', () => sqlFormat(sqlParens(1000), { language: 'sql', tabWidth: 2 }));
run('d4-sql-long-inlist', 'SQL 单行 20000 个 IN 元素（超长行）', () =>
  sqlFormat(`select * from t where a in (${Array.from({ length: 20000 }, (_, i) => i).join(',')});`, {
    language: 'sql',
    tabWidth: 2,
  })
);

// XML：n 层嵌套
function xmlNest(n) {
  return '<a>'.repeat(n) + 'text' + '</a>'.repeat(n);
}
run('d5-xml-nest-200', 'XML 200 层嵌套（格式化）', () => xmlFormat(xmlNest(200), { lineSeparator: '\n' }));
run('d6-xml-nest-1000', 'XML 1000 层嵌套（格式化）', () => xmlFormat(xmlNest(1000), { lineSeparator: '\n' }));
run('d7-xml-nest-5000', 'XML 5000 层嵌套（格式化）', () => xmlFormat(xmlNest(5000), { lineSeparator: '\n' }));
run('d8-xml-nest-200-minify', 'XML 200 层嵌套（压缩）', () =>
  xmlFormat.minify(xmlNest(200), {})
);
run('d9-xml-wide-20000', 'XML 20000 个兄弟节点（宽输入）', () =>
  xmlFormat(`<r>${'<a>1</a>'.repeat(20000)}</r>`, { lineSeparator: '\n' })
);

fs.writeFileSync(OUT, JSON.stringify({ rows }, null, 2), 'utf8');
console.log(`\n已写出：${OUT}`);
