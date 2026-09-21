/**
 * 无 Web Crypto 时的短哈希回退（FNV-1a 变体，非加密用途）
 *
 * 仅在非安全上下文（`crypto.subtle` 不可用）时使用。
 * 不是安全哈希，只用于缓存键与日志脱敏 —— 见 `utils/hash.ts`。
 */
export function createHashFallback(input: string): string {
  // 两路不同种子的 FNV-1a，拼成 16 位十六进制，降低碰撞概率
  let h1 = 0x811c9dc5;
  let h2 = 0x01000193;
  for (let i = 0; i < input.length; i++) {
    const c = input.charCodeAt(i);
    h1 ^= c;
    h1 = Math.imul(h1, 0x01000193);
    h2 ^= c + i;
    h2 = Math.imul(h2, 0x85ebca6b);
  }
  const a = (h1 >>> 0).toString(16).padStart(8, '0');
  const b = (h2 >>> 0).toString(16).padStart(8, '0');
  return a + b;
}
