/**
 * 工具箱第 6 项「JSON 格式化 / 压缩」负向对照（纯函数部分）—— Phase 6（v1.6.0 · AC-29）
 *
 * 判据出处（逐字符）：`文档记录/需求文档/v1.6.0.md` AC-29 原文 ——
 *   JSON：`{"a":{"b":[1,2]}}` 的**格式化**输出严格等于
 *   `{\n  "a": {\n    "b": [\n      1,\n      2\n    ]\n  }\n}`；
 *   **压缩**输出严格等于 `{"a":{"b":[1,2]}}`；
 *   输入**非法 JSON**（如 `{"a":}`）→ 出现错误提示且原文**逐字符不变** `[负向对照]`
 *
 * 实现口径（`文档记录/设计文档/v1.6.0.md` §4.1.6 第 6 项）：格式化缩进 **2 空格**；
 * 压缩去结构外空白；非法 JSON **只提示错误、原文逐字符不变** ⇒ 格式化 / 压缩
 * **要么返回新串、要么抛错，绝不返回"部分修复"的结果**（`toolbox.ts:290-292`）。
 *
 * 树视图 DOM（`a → b → 数组(2 个元素)`）由另一位负责，本文件**不**碰 DOM。
 *
 * `[负向对照]` 锚点：① 格式化结果必须真的换行缩进（不许原样返回）；
 * ② 非法输入下必须抛错（不许返回半成品）；③ 错误信息里**不得回显文档内容**（安全宪法 §9.14）。
 */
import { describe, expect, it } from 'vitest';
import { formatJson, minifyJson, tryParseJson } from '../toolbox';

/** 需求 AC-29 的原文样本 */
const SAMPLE = '{"a":{"b":[1,2]}}';
const FORMATTED = '{\n  "a": {\n    "b": [\n      1,\n      2\n    ]\n  }\n}';
const MINIFIED = '{"a":{"b":[1,2]}}';
/** 需求原文点名的非法 JSON 样本 */
const BAD = '{"a":}';

/** 把"调用必须抛错"落成可比较的字符串：返回错误信息；没有抛错则返回空串（断言会失败） */
function messageOf(fn: () => unknown): string {
  try {
    fn();
  } catch (error) {
    return error instanceof Error ? error.message : String(error);
  }
  return '';
}

describe('AC-29 JSON 格式化 / 压缩（需求原文样本，逐字符判据）', () => {
  it('格式化 ⇒ 2 空格缩进的原文期望串（严格相等）', () => {
    expect(formatJson(SAMPLE)).toBe(FORMATTED);
  });

  it('压缩 ⇒ 去结构外空白的原文期望串（严格相等）', () => {
    expect(minifyJson(SAMPLE)).toBe(MINIFIED);
  });

  it('格式化与压缩互为同一语义对象：`minify(format(x)) === minify(x)`', () => {
    expect(minifyJson(formatJson(SAMPLE))).toBe(MINIFIED);
  });

  it('tryParseJson 成功分支：`ok:true` 且 `value` 与 JSON.parse 一致', () => {
    const result = tryParseJson(SAMPLE);
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error('样本应当是合法 JSON');
    expect(result.value).toEqual({ a: { b: [1, 2] } });
  });
});

describe('AC-29 非法 JSON（需求原文样本，按实现真实字段断言）', () => {
  it('tryParseJson ⇒ `{ok:false, error}`，错误信息为固定形状的提示', () => {
    const result = tryParseJson(BAD);
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error('非法 JSON 不应解析成功');
    // 实现字段就是 `error`（`toolbox.ts:295-297` 的 JsonParseResult 联合类型）
    expect(typeof result.error).toBe('string');
    // 形状：`JSON 解析失败（第 N 个字符附近）` 或 `JSON 解析失败（未知位置）`
    // （后者出现在 Node ≥ 20 的新版 V8 上：JSON.parse 的报错文本已不含 `position`）
    expect(result.error).toMatch(/^JSON 解析失败（(第 \d+ 个字符附近|未知位置)）$/);
  });

  it('错误信息不回显文档内容（安全宪法 §9.14：不带用户输入片段）', () => {
    const result = tryParseJson(BAD);
    if (result.ok) throw new Error('非法 JSON 不应解析成功');
    expect(result.error).not.toContain(BAD);
    expect(result.error).not.toContain('a');
  });

  it('格式化 / 压缩在非法输入下**必须抛错**（不得返回"半成品"）', () => {
    const result = tryParseJson(BAD);
    if (result.ok) throw new Error('非法 JSON 不应解析成功');
    // 逐字符严格：抛出的信息必须等于预检给出的信息
    expect(messageOf(() => formatJson(BAD))).toBe(result.error);
    expect(messageOf(() => minifyJson(BAD))).toBe(result.error);
  });
});

describe('AC-29 负向对照锚点', () => {
  it('格式化必须真的重排（不许原样返回），压缩必须真的去空白', () => {
    expect(formatJson(SAMPLE)).not.toBe(SAMPLE);
    expect(formatJson(SAMPLE)).not.toBe(MINIFIED);
    expect(minifyJson('{\n  "a": 1\n}')).toBe('{"a":1}');
    // 压缩不是"trim 一下"：结构内部的空白也必须消失，且不引入换行
    expect(minifyJson(SAMPLE)).not.toContain('\n');
    expect(minifyJson(SAMPLE)).not.toContain(' ');
  });

  it('非法输入的"原文逐字符不变"：纯字符串入参不可能被就地改写，等价的可执行判据是"抛错而非返回"', () => {
    // ① 入参对象在调用前后逐字符相同（JS 字符串不可变，这里锁死"实现没有拿它做可变中转")
    const input = BAD;
    expect(() => minifyJson(input)).toThrow();
    expect(input).toBe('{"a":}');
    // ② 返回值为"部分修复结果"的情形必须不存在：调用只能抛错，不能落进 then/返回值
    let returned: unknown = 'NEVER-SET';
    try {
      returned = minifyJson(input);
    } catch {
      /* 预期路径 */
    }
    expect(returned).toBe('NEVER-SET');
  });
});
