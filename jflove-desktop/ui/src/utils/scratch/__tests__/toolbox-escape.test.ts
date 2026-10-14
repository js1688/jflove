/**
 * 工具箱第 4 项「转义 / 反转义（JSON · HTML · URL）」负向对照 —— Phase 6（v1.6.0 · AC-27）
 *
 * 判据出处（逐字符）：`文档记录/需求文档/v1.6.0.md` AC-27 原文 ——
 *   **JSON 转义**：`a"b\c` → `a\"b\\c`；
 *   **HTML 转义**：`<a href="x">&` → `&lt;a href=&quot;x&quot;&gt;&amp;`；
 *   **URL 转义**：`a b&c=中文` → `a%20b%26c%3D%E4%B8%AD%E6%96%87`；
 *   三组各自「转义 → 反转义」后**等于原文** `[负向对照]`
 *
 * 实现口径（`文档记录/设计文档/v1.6.0.md` §4.1.6 第 4 项）：
 *   JSON 只处理 `"`→`\"`、`\`→`\\`、换行→`\n`（**不含外层引号**）；
 *   HTML 只处理 `& < > "`（`&` 必须**最先**替换，否则会二次转义）；
 *   URL 保留 `A-Za-z0-9-_.!~*'()`，其余按 **UTF-8 百分号编码**。
 *
 * `[负向对照]` 锚点：`&` 先于 `<` 替换（否则产出 `&amp;lt;`）、
 * 单引号**不**被 HTML 转义（设计只列 4 个字符）、三组往返都必须回到原文。
 */
import { describe, expect, it } from 'vitest';
import {
  decodeUrlComponent,
  encodeUrlComponent,
  escapeHtml,
  escapeJson,
  unescapeHtml,
  unescapeJson,
} from '../toolbox';

/** 需求 AC-27 的原文样本（JS 字面量：`a"b\c` 写作 `'a"b\\c'`） */
const JSON_SAMPLE = 'a"b\\c';
const HTML_SAMPLE = '<a href="x">&';
const URL_SAMPLE = 'a b&c=中文';

describe('AC-27 JSON 转义（需求原文样本，逐字符判据）', () => {
  it('`a"b\\c` ⇒ `a\\"b\\\\c`（严格相等）', () => {
    expect(escapeJson(JSON_SAMPLE)).toBe('a\\"b\\\\c');
  });

  it('“转义 → 反转义”后等于原文', () => {
    expect(unescapeJson(escapeJson(JSON_SAMPLE))).toBe(JSON_SAMPLE);
  });

  it('换行仍按 `\\n` 转义；未知转义序列反转义时原样保留', () => {
    expect(escapeJson('a\nb')).toBe('a\\nb');
    // `\q` 不是设计列出的转义序列 ⇒ unescapeJson 不得把它变成别的字符
    expect(unescapeJson('a\\qb')).toBe('a\\qb');
  });
});

describe('AC-27 HTML 转义（需求原文样本，逐字符判据）', () => {
  it('`<a href="x">&` ⇒ `&lt;a href=&quot;x&quot;&gt;&amp;`（严格相等）', () => {
    expect(escapeHtml(HTML_SAMPLE)).toBe('&lt;a href=&quot;x&quot;&gt;&amp;');
  });

  it('“转义 → 反转义”后等于原文', () => {
    expect(unescapeHtml(escapeHtml(HTML_SAMPLE))).toBe(HTML_SAMPLE);
  });
});

describe('AC-27 URL 转义（需求原文样本，逐字符判据）', () => {
  it('`a b&c=中文` ⇒ `a%20b%26c%3D%E4%B8%AD%E6%96%87`（严格相等）', () => {
    expect(encodeUrlComponent(URL_SAMPLE)).toBe('a%20b%26c%3D%E4%B8%AD%E6%96%87');
  });

  it('“转义 → 反转义”后等于原文', () => {
    expect(decodeUrlComponent(encodeUrlComponent(URL_SAMPLE))).toBe(URL_SAMPLE);
  });
});

describe('AC-27 负向对照锚点', () => {
  it('`&` 必须最先替换：`<&` ⇒ `&lt;&amp;`，不得出现二次转义的 `&amp;lt;`', () => {
    const escaped = escapeHtml('<&');
    expect(escaped).toBe('&lt;&amp;');
    expect(escaped).not.toContain('&amp;lt;');
    expect(escaped).not.toContain('&amp;amp;');
  });

  it('HTML 转义只认设计列出的 4 个字符：单引号原样保留', () => {
    expect(escapeHtml("it's \"x\"")).toBe("it's &quot;x&quot;");
  });

  it('URL 转义保留设计列出的安全字符（`-_.!~*` 与单引号括弧 不得被百分号编码）', () => {
    expect(encodeUrlComponent("-_.!~*'()")).toBe("-_.!~*'()");
    // 而空格与 `&` / `=` 必须被编码（与 AC 样本同口径）
    expect(encodeUrlComponent(' ')).toBe('%20');
    expect(encodeUrlComponent('&')).toBe('%26');
    expect(encodeUrlComponent('=')).toBe('%3D');
  });
});
