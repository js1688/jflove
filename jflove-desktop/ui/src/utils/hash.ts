/**
 * 内容哈希（用于图表缓存键、日志脱敏）
 *
 * 用途约束（AGENTS.md §9.4）：图表源码属于笔记正文，**不得**出现在日志里；
 * 日志只允许打哈希前缀。因此这里提供的是一个稳定的短哈希。
 */
import { createHashFallback } from './hash-fallback';

/**
 * 计算短哈希（16 位十六进制）。
 *
 * 优先用 Web Crypto 的 SHA-256（异步）；不可用时回落到内置的非加密哈希。
 * 注意：这不是安全用途，只用于缓存键与日志脱敏。
 */
export async function shortHash(input: string): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (subtle) {
    try {
      const data = new TextEncoder().encode(input);
      const digest = await subtle.digest('SHA-256', data);
      const bytes = new Uint8Array(digest);
      let hex = '';
      for (let i = 0; i < 8; i++) hex += bytes[i].toString(16).padStart(2, '0');
      return hex;
    } catch {
      // 某些环境（非安全上下文）会抛错，落到下面的回退
    }
  }
  return createHashFallback(input);
}

/** 同步短哈希（用于无法 await 的场景，例如渲染前的即时占位） */
export function shortHashSync(input: string): string {
  return createHashFallback(input);
}
