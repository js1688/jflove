/**
 * 工具箱第 9 项「SQL 美化」负向对照 —— Phase 6（v1.6.0 · AC-32）
 *
 * 判据出处（逐字符）：`文档记录/需求文档/v1.6.0.md` AC-32 原文 ——
 *   SQL 美化（输入 `select a,b from t where x=1`）：关键字大小写=**大写** → 输出含 `SELECT`
 *   且**不含**小写 `select`；=**小写** → 相反；缩进宽度=**4** → 第一层缩进行前导空格数为 4；
 *   **方言**切换（至少 2 个可选）后美化仍成功且标识符 `a`/`b`/`t`/`x` 名称不变 `[负向对照]`
 *
 * 执行方式：**真调离线包**（`public/vendor/editor.bundle.js` 的 `window.__JFLOVE_CM__.formatters.sql`，
 * 即 `sql-formatter@15.9.0`），参数由 `toSqlFormatOptions` 做映射。注入方式与理由见
 * `src/test/cm-bundle.ts`（jsdom 不加载外部 `<script>`，故用 `node:fs` + `new Function`）。
 *
 * 实现口径（`文档记录/设计文档/v1.6.0.md` §4.1.6 第 9 项 + R-6 定稿参数）：
 *   至少可配「方言 / 关键字大小写 / 缩进宽度」三项；`tabWidth` 取整并钳到 ≥ 0；
 *   美化前必须过 `checkSqlFormattable` 形态预检（否则库会静默改写非 SQL 文本，R-6 第 ③ 条）。
 *
 * `[负向对照]` 锚点：关键字大小写两个方向都要"只出现一种形态"；
 * 缩进宽度必须真的影响输出（4 与 2 的输出必须不同）；标识符不得被顺手改大小写。
 */
import { describe, expect, it } from 'vitest';
import {
  DEFAULT_SQL_FORMAT_OPTIONS,
  SQL_MAX_INPUT_LENGTH,
  SQL_MAX_PAREN_DEPTH,
  checkSqlFormattable,
  toSqlFormatOptions,
} from '../toolbox';
import { loadCmFormatters } from '../../../test/cm-bundle';

/** 需求 AC-32 的原文样本 */
const SAMPLE = 'select a,b from t where x=1';

/** 取输出里所有"标识符样"的 token（用于断言名称不变） */
function tokensOf(text: string): string[] {
  return text.split(/[^A-Za-z_0-9]+/).filter((token) => token.length > 0);
}

/** 某行前导空格数 */
function leadingSpaces(line: string): number {
  return line.length - line.trimStart().length;
}

const formatters = loadCmFormatters();

describe('AC-32 SQL 美化（需求原文样本，真调离线包）', () => {
  it('关键字=大写 ⇒ 含 `SELECT`，且不含小写 `select`', () => {
    const output = formatters.sql(
      SAMPLE,
      toSqlFormatOptions({ language: 'sql', keywordCase: 'upper', tabWidth: 2 }),
    );
    expect(output).toContain('SELECT');
    expect(output).not.toContain('select');
    // 其余关键字同向：FROM / WHERE 也必须是大写
    expect(output).toContain('FROM');
    expect(output).toContain('WHERE');
  });

  it('关键字=小写 ⇒ 相反：含 `select`，且不含大写 `SELECT`', () => {
    const output = formatters.sql(
      SAMPLE,
      toSqlFormatOptions({ language: 'sql', keywordCase: 'lower', tabWidth: 2 }),
    );
    expect(output).toContain('select');
    expect(output).not.toContain('SELECT');
    expect(output).toContain('from');
    expect(output).toContain('where');
  });

  it('缩进宽度=4 ⇒ 第一层缩进行前导空格数为 4（且不再出现 2 空格层）', () => {
    const output = formatters.sql(
      SAMPLE,
      toSqlFormatOptions({ language: 'sql', keywordCase: 'upper', tabWidth: 4 }),
    );
    const lines = output.split('\n');
    expect(lines[0]).toBe('SELECT');
    // 第一层缩进行 = 紧随 `SELECT` 之后的那一行（`a,`）
    expect(leadingSpaces(lines[1])).toBe(4);
    expect(lines[1].startsWith('    a,')).toBe(true);
    // 不允许残留"2 空格层"（tabWidth 必须真的生效，而不是被写死成 2）
    expect(lines.some((line) => /^ {2}\S/.test(line))).toBe(false);
  });

  it('缩进宽度切换必须真的改变输出：2 与 4 的输出不相等', () => {
    const two = formatters.sql(
      SAMPLE,
      toSqlFormatOptions({ language: 'sql', keywordCase: 'upper', tabWidth: 2 }),
    );
    const four = formatters.sql(
      SAMPLE,
      toSqlFormatOptions({ language: 'sql', keywordCase: 'upper', tabWidth: 4 }),
    );
    expect(two).not.toBe(four);
    expect(leadingSpaces(two.split('\n')[1])).toBe(2);
  });

  it('方言切换（至少 2 个可选）后美化仍成功，且标识符 `a`/`b`/`t`/`x` 名称不变', () => {
    const dialects = formatters.sqlDialects;
    expect(dialects.length).toBeGreaterThanOrEqual(2);
    // 取两个确实存在的方言（不依赖数组顺序），逐个真跑
    const chosen = ['sql', 'postgresql'].filter((name) => dialects.includes(name));
    expect(chosen.length).toBe(2);

    for (const language of chosen) {
      const output = formatters.sql(
        SAMPLE,
        toSqlFormatOptions({ language, keywordCase: 'upper', tabWidth: 2 }),
      );
      expect(output).toContain('SELECT');
      const tokens = tokensOf(output);
      for (const identifier of ['a', 'b', 't', 'x']) {
        expect(tokens).toContain(identifier);
      }
      // 标识符不许被顺手大写（`identifierCase: 'preserve'`）
      expect(tokens).not.toContain('A');
      expect(tokens).not.toContain('B');
      expect(tokens).not.toContain('T');
      expect(tokens).not.toContain('X');
    }
  });

  it('不支持的方言必须抛错（离线包 `Unsupported SQL dialect` 守卫贯通到调用方）', () => {
    expect(() =>
      formatters.sql(SAMPLE, toSqlFormatOptions({ language: 'not-a-dialect', keywordCase: 'upper', tabWidth: 2 })),
    ).toThrow(/Unsupported SQL dialect/i);
  });
});

describe('AC-32 `toSqlFormatOptions` 参数映射', () => {
  it('默认配置（大写 + 2 空格）映射后与 `DEFAULT_SQL_FORMAT_OPTIONS` 深相等', () => {
    expect(toSqlFormatOptions({ language: 'sql', keywordCase: 'upper', tabWidth: 2 })).toEqual(
      DEFAULT_SQL_FORMAT_OPTIONS,
    );
  });

  it('`tabWidth` 取整并钳到 ≥ 0，`useTabs` 恒为 false（缩进单位只能是空格）', () => {
    expect(toSqlFormatOptions({ language: 'sql', keywordCase: 'upper', tabWidth: 3.9 }).tabWidth).toBe(3);
    expect(toSqlFormatOptions({ language: 'sql', keywordCase: 'upper', tabWidth: -5 }).tabWidth).toBe(0);
    expect(toSqlFormatOptions({ language: 'sql', keywordCase: 'lower', tabWidth: 4 }).useTabs).toBe(false);
    expect(toSqlFormatOptions({ language: 'sqlite', keywordCase: 'lower', tabWidth: 4 }).language).toBe('sqlite');
  });
});

describe('AC-32 前置预检（负向对照：不合规输入必须被挡住，不得交给库）', () => {
  it('需求原文样本本身必须通过预检（否则上面的 ACL 断言无从谈起）', () => {
    expect(checkSqlFormattable(SAMPLE)).toEqual({ ok: true });
  });

  it('空内容 / MySQL `DELIMITER` / 命名参数 / 非 SQL 文本 一律拒绝并给中文提示', () => {
    expect(checkSqlFormattable('   \n  ')).toEqual({ ok: false, reason: '内容为空，无需美化' });
    expect(checkSqlFormattable('DELIMITER ;;').reason).toContain('DELIMITER');
    expect(checkSqlFormattable('select * from t where id = :id').reason).toContain('命名参数');
    // R-6 第 ③ 条实测样本：XML 文本会被 sql-formatter 静默改写 ⇒ 必须挡在库外面
    const xmlLike = '<root><a>1</a></root>';
    expect(checkSqlFormattable(xmlLike).ok).toBe(false);
    expect(checkSqlFormattable(xmlLike).reason).toContain('未识别到 SQL 关键词');
  });

  it('超出输入上限 / 括号嵌套过深 一律拒绝，且提示里带出该上限常量', () => {
    const tooLong = `select ${'x'.repeat(SQL_MAX_INPUT_LENGTH)}`;
    expect(tooLong.length).toBeGreaterThan(SQL_MAX_INPUT_LENGTH);
    const longResult = checkSqlFormattable(tooLong);
    expect(longResult.ok).toBe(false);
    expect(longResult.reason).toContain(String(SQL_MAX_INPUT_LENGTH));

    const tooDeep = `select ${'('.repeat(SQL_MAX_PAREN_DEPTH + 1)}${')'.repeat(SQL_MAX_PAREN_DEPTH + 1)}`;
    const deepResult = checkSqlFormattable(tooDeep);
    expect(deepResult.ok).toBe(false);
    expect(deepResult.reason).toContain('括号嵌套过深');
    // 上限之内的同形输入必须放行（证明拒绝来自深度判据，不是别的分支）
    const atLimit = `select ${'('.repeat(SQL_MAX_PAREN_DEPTH)}${')'.repeat(SQL_MAX_PAREN_DEPTH)}`;
    expect(checkSqlFormattable(atLimit).ok).toBe(true);
  });
});
