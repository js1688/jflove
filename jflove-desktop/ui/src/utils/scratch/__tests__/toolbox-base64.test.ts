/**
 * 工具箱第 5 项「Base64 编解码」负向对照 —— Phase 6（v1.6.0 · AC-28）
 *
 * 判据出处（逐字符）：`文档记录/需求文档/v1.6.0.md` AC-28 原文 ——
 *   Base64：`"你好, jflove"`（UTF-8）编码结果**严格等于** `"5L2g5aW9LCBqZmxvdmU="`；
 *   该串解码后等于原文 `[负向对照]`
 *
 * 实现口径（`文档记录/设计文档/v1.6.0.md` §4.1.6 第 5 项）：
 *   UTF-8 往返（`TextEncoder` / `TextDecoder`，**不用 `btoa` 直接吃字符串**——那样非 Latin-1
 *   字符会抛 `InvalidCharacterError`）。
 *
 * 为什么这条 AC 是有效的鉴伪判据：`"你好, jflove"` 的 **UTF-16 Latin-1 视角**编码
 * （即 `btoa('你好, jflove')` 那种错误做法能得到的东西）与 UTF-8 结果**不相等**；
 * 只有真的先做 UTF-8 字节化才会命中期望串。
 */
import { describe, expect, it } from 'vitest';
import { decodeBase64, encodeBase64 } from '../toolbox';

/** 需求 AC-28 的原文样本与期望串 */
const SAMPLE = '你好, jflove';
const SAMPLE_BASE64 = '5L2g5aW9LCBqZmxvdmU=';

describe('AC-28 Base64（需求原文样本，逐字符判据）', () => {
  it('UTF-8 编码 ⇒ "5L2g5aW9LCBqZmxvdmU="（严格相等）', () => {
    expect(encodeBase64(SAMPLE)).toBe(SAMPLE_BASE64);
  });

  it('期望串解码 ⇒ 原文（严格相等）', () => {
    expect(decodeBase64(SAMPLE_BASE64)).toBe(SAMPLE);
  });

  it('“编码 → 解码”往返等于原文', () => {
    expect(decodeBase64(encodeBase64(SAMPLE))).toBe(SAMPLE);
  });
});

describe('AC-28 负向对照锚点', () => {
  it('纯 ASCII 往返稳定（空串 ⇒ 空串，不做任何补齐/填充）', () => {
    expect(encodeBase64('hello')).toBe('aGVsbG8=');
    expect(decodeBase64('aGVsbG8=')).toBe('hello');
    expect(encodeBase64('')).toBe('');
    expect(decodeBase64('')).toBe('');
  });

  it('非法 Base64 字符集 ⇒ 抛错，绝不返回"半个结果"', () => {
    expect(() => decodeBase64('!!!not-base64!!!')).toThrow('Base64 解码失败：含非法字符');
  });

  it('字节序列不是合法 UTF-8 ⇒ 抛错（`/w==` 是 0xFF，UTF-8 里的非法首字节）', () => {
    expect(() => decodeBase64('/w==')).toThrow('Base64 解码失败：不是合法的 UTF-8 字节序列');
  });
});
