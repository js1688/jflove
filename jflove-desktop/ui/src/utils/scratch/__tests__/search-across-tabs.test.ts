/**
 * v1.6.2 F15 用例：跨标签搜索 / 替换的**纯逻辑**（AC-92 / AC-94 / AC-95）
 *
 * 判据形状：本文件不渲染任何组件、不 import 桥 / 服务层 ⇒ 结构上就证明了
 * **跨标签搜索不引入任何新能力面**（AC-96 的一半；另一半由组件用例的桥替身守着）。
 */

import { describe, expect, it } from 'vitest';
import {
  MAX_HITS_PER_TAB,
  buildSearchPattern,
  escapeRegExp,
  findHits,
  patternMatchesEmpty,
  replaceAllLiteral,
  searchTabs,
} from '../search-across-tabs';
import type { SearchableTab } from '../search-across-tabs';

const OPTIONS = { regex: false, caseSensitive: false, wholeWord: false };

function patternFor(query: string, overrides: Partial<typeof OPTIONS> = {}) {
  const pattern = buildSearchPattern(query, { ...OPTIONS, ...overrides });
  if (!pattern) throw new Error(`模式构造失败：${query}`);
  return pattern;
}

describe('buildSearchPattern：字面量 / 正则 / 大小写 / 整词', () => {
  it('字面量模式把正则元字符转义（不能当成正则解释）', () => {
    expect(escapeRegExp('a.b*c')).toBe('a\\.b\\*c');
    const hits = findHits('a.b*c axbbc', patternFor('a.b*c'));
    expect(hits).toHaveLength(1);
    expect(hits[0].from).toBe(0);
  });

  it('正则模式下非法正则返回 `null`（调用方给中文提示，不抛异常）', () => {
    expect(buildSearchPattern('([', { ...OPTIONS, regex: true })).toBeNull();
    expect(buildSearchPattern('', OPTIONS)).toBeNull(); // 空查询
  });

  it('大小写敏感开关真的生效', () => {
    expect(findHits('Foo foo', patternFor('foo'))).toHaveLength(2);
    expect(findHits('Foo foo', patternFor('foo', { caseSensitive: true }))).toHaveLength(1);
  });

  it('整词开关真的生效', () => {
    expect(findHits('foo foobar', patternFor('foo'))).toHaveLength(2);
    expect(findHits('foo foobar', patternFor('foo', { wholeWord: true }))).toHaveLength(1);
  });

  it('零长度匹配可被识别（用于挡掉"在每个字符之间插入内容"）', () => {
    // 空查询本身被 `buildSearchPattern` 拒掉（返回 null）；这里直接构造"能匹配空串"的模式
    expect(patternMatchesEmpty(/(?:)/g)).toBe(true);
    expect(patternMatchesEmpty(/a?/g)).toBe(true);
    expect(patternMatchesEmpty(patternFor('a'))).toBe(false);
  });
});

describe('findHits：偏移 / 行号 / 行文本', () => {
  it('多行命中的偏移与 1 基行号正确（偏移口径 = UTF-16 码元，与 CM6 位置同源）', () => {
    const text = 'foo\nbar\nfoo baz\n';
    const hits = findHits(text, patternFor('foo'));
    expect(hits.map((hit) => hit.from)).toEqual([0, 8]);
    expect(hits.map((hit) => hit.line)).toEqual([1, 3]);
    expect(hits.map((hit) => hit.to)).toEqual([3, 11]);
    expect(hits[0].lineText).toBe('foo');
    expect(hits[1].lineText).toBe('foo baz');
  });

  it('零长度模式不会死循环（`/(?:)/g` 也必须有界）', () => {
    const hits = findHits('abc', /(?:)/g, 10);
    expect(hits.length).toBeLessThanOrEqual(10);
  });

  it('命中数受 `limit` 截断（调用方据此提示"命中过多"）', () => {
    const text = Array.from({ length: 50 }, () => 'x').join('\n');
    expect(findHits(text, patternFor('x'), 5)).toHaveLength(5);
  });

  it('空正文返回空数组（不抛）', () => {
    expect(findHits('', patternFor('a'))).toEqual([]);
  });
});

describe('replaceAllLiteral：计数与替换同源、按字面插入', () => {
  it('计数等于实际替换处数', () => {
    const outcome = replaceAllLiteral('foo foo foo', patternFor('foo'), 'bar');
    expect(outcome.text).toBe('bar bar bar');
    expect(outcome.count).toBe(3);
  });

  it('替换文本**按字面插入**（`$1` 不被展开 —— 界面已写明这一口径）', () => {
    const outcome = replaceAllLiteral('foo', patternFor('foo'), '$1bar');
    expect(outcome.text).toBe('$1bar');
    expect(outcome.count).toBe(1);
  });

  it('替换后再次搜索 ⇒ 0 命中（AC-95② 的核心判据）', () => {
    const outcome = replaceAllLiteral('a foo b\nfoo', patternFor('foo'), 'bar');
    expect(findHits(outcome.text, patternFor('foo'))).toHaveLength(0);
  });

  it('大小写敏感替换不会误伤不同大小写的命中', () => {
    const outcome = replaceAllLiteral('Foo foo', patternFor('foo', { caseSensitive: true }), 'bar');
    expect(outcome.text).toBe('Foo bar');
    expect(outcome.count).toBe(1);
  });
});

describe('searchTabs：跨标签聚合（顺序 = 传入顺序 = 标签条顺序）', () => {
  const tabs: SearchableTab[] = [
    { tabId: 'a', title: 'a.txt', text: 'foo\nbar\n' },
    { tabId: 'b', title: 'b.txt', text: 'nothing here\n' },
    { tabId: 'c', title: 'c.txt', text: 'foo\nfoo\n' },
  ];

  it('按标签页分组、只保留有命中的组，总计与涉及页数正确', () => {
    const result = searchTabs(tabs, patternFor('foo'));
    expect(result.groups.map((group) => group.tabId)).toEqual(['a', 'c']);
    expect(result.total).toBe(3);
    expect(result.searchedTabs).toBe(3);
    expect(result.truncated).toBe(false);
  });

  it('未挂载的标签页根本不进入搜索数组 ⇒ 不会产生任何命中（AC-94 的结构保证）', () => {
    const result = searchTabs(tabs.slice(0, 1), patternFor('foo'));
    expect(result.searchedTabs).toBe(1);
    expect(result.groups).toHaveLength(1);
  });

  it('总命中触顶时置 `truncated`（界面据此提示，不静默少报）', () => {
    const many: SearchableTab[] = [
      { tabId: 'x', title: 'x', text: 'x\nx\nx\n' },
      { tabId: 'y', title: 'y', text: 'x\nx\n' },
    ];
    const result = searchTabs(many, patternFor('x'), MAX_HITS_PER_TAB, 4);
    expect(result.total).toBe(4);
    expect(result.truncated).toBe(true);
  });
});
