/**
 * 工具箱第 3 项「大小写转换」负向对照 —— Phase 6（v1.6.0 · AC-26）
 *
 * 判据出处（逐字符）：`文档记录/需求文档/v1.6.0.md` AC-26 原文 ——
 *   输入 `"hello world_foo-bar"`：**大写** → `"HELLO WORLD_FOO-BAR"`；
 *   **小写** → `"hello world_foo-bar"`；**驼峰** → `"helloWorldFooBar"`；
 *   **下划线** → `"hello_world_foo_bar"` `[负向对照]`
 *
 * 实现口径（`文档记录/设计文档/v1.6.0.md` §4.1.6 第 3 项）：
 *   分隔符集合 = 空白符 / `_` / `-` / `.`；驼峰 = 切分后**首词全小写**、其余词首字母大写拼接；
 *   下划线 = 切分后全小写 + `_` 连接。
 *
 * `[负向对照]` 锚点：本文件最后两条断言锁死"不许顺手归一化"——
 * 大写**只**改字母大小写（`_` 与 `-` 原样保留）、驼峰结果里不许残留 `_` / `-` / 空格。
 */
import { describe, expect, it } from 'vitest';
import { splitWords, toCamelCase, toLowerCase, toSnakeCase, toUpperCase } from '../toolbox';

/** 需求 AC-26 的原文样本 */
const SAMPLE = 'hello world_foo-bar';

describe('AC-26 大小写转换（需求原文样本，逐字符判据）', () => {
  it('大写 ⇒ "HELLO WORLD_FOO-BAR"（严格相等）', () => {
    expect(toUpperCase(SAMPLE)).toBe('HELLO WORLD_FOO-BAR');
  });

  it('小写 ⇒ "hello world_foo-bar"（原文本来就是小写，结果必须与原文逐字符相同）', () => {
    expect(toLowerCase(SAMPLE)).toBe('hello world_foo-bar');
    expect(toLowerCase(SAMPLE)).toBe(SAMPLE);
  });

  it('驼峰 ⇒ "helloWorldFooBar"（严格相等）', () => {
    expect(toCamelCase(SAMPLE)).toBe('helloWorldFooBar');
  });

  it('下划线 ⇒ "hello_world_foo_bar"（严格相等）', () => {
    expect(toSnakeCase(SAMPLE)).toBe('hello_world_foo_bar');
  });

  it('分词口径：空白符 / `_` / `-` / `.` 都是分隔符，空词丢弃', () => {
    expect(splitWords(SAMPLE)).toEqual(['hello', 'world', 'foo', 'bar']);
    // `.` 也在设计 §4.1.6 第 3 项的分隔符集合里
    expect(splitWords('a.b c-d')).toEqual(['a', 'b', 'c', 'd']);
  });
});

describe('AC-26 负向对照锚点（防止"顺手归一化"式实现蒙混过关）', () => {
  it('大写只动字母大小写：`_` 与 `-` 必须逐字符保留', () => {
    const upper = toUpperCase(SAMPLE);
    expect(upper).toContain('WORLD_FOO-BAR');
    // 若实现顺手把 `-` 换成 `_`（或反过来），下面两条必须失败
    expect(upper).not.toContain('WORLD_FOO_BAR');
    expect(upper.match(/_/g)?.length).toBe(1);
    expect(upper.match(/-/g)?.length).toBe(1);
  });

  it('驼峰结果里不许残留任何分隔符（`_` / `-` / 空格）', () => {
    const camel = toCamelCase(SAMPLE);
    expect(camel).not.toContain('_');
    expect(camel).not.toContain('-');
    expect(camel).not.toContain(' ');
    // 也不能把原文原样或大写化后返回
    expect(camel).not.toBe(SAMPLE);
    expect(camel).not.toBe('HelloWorldFooBar');
  });
});
