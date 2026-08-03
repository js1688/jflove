import { describe, it, expect } from 'vitest';
import {
  arrayBufferToBase64,
  base64ToUint8Array,
  generateKeyPair,
  deriveSessionKey,
  encryptEnvelope,
  decryptEnvelope,
  generateKeyPairJS,
  deriveSessionKeyJS,
} from '../../src/utils/crypto';

describe('crypto', () => {
  it('Base64 编解码互逆', () => {
    const original = new Uint8Array([0, 1, 2, 255, 128, 64, 32, 16]);
    const b64 = arrayBufferToBase64(original);
    const decoded = base64ToUint8Array(b64);
    expect(decoded).toEqual(original);
  });

  it('生成 X25519 密钥对（Web Crypto 或纯 JS 回退）', async () => {
    const { publicKeyB64, privateKey } = await generateKeyPair();
    expect(publicKeyB64).toHaveLength(44); // 32 bytes raw → 44 base64 chars
    // 安全上下文为 CryptoKey，非安全上下文为 32B Uint8Array（回退）
    expect(privateKey).toBeTruthy();
    if (privateKey instanceof Uint8Array) {
      expect(privateKey).toHaveLength(32);
    }
  });

  it('ECDH + HKDF 派生相同的 session_key', async () => {
    const alice = await generateKeyPair();
    const bob = await generateKeyPair();

    const aliceKey = await deriveSessionKey(alice.privateKey, bob.publicKeyB64);
    const bobKey = await deriveSessionKey(bob.privateKey, alice.publicKeyB64);

    expect(aliceKey).toEqual(bobKey);
    expect(aliceKey).toHaveLength(32);
  });

  it('纯 JS 回退：X25519 + HKDF 派生相同的 session_key（HTTP 非安全上下文兼容）', () => {
    const alice = generateKeyPairJS();
    const bob = generateKeyPairJS();

    expect(alice.publicKeyB64).toHaveLength(44);
    expect(alice.privateKey).toHaveLength(32);

    const aliceKey = deriveSessionKeyJS(alice.privateKey, bob.publicKeyB64);
    const bobKey = deriveSessionKeyJS(bob.privateKey, alice.publicKeyB64);

    expect(aliceKey).toEqual(bobKey);
    expect(aliceKey).toHaveLength(32);
  });

  it('纯 JS 与 Web Crypto 跨实现互操作：session_key 一致（与后端/移动端互操作等价验证）', async () => {
    // 一端纯 JS 私钥，一端 Web Crypto 密钥，交叉 ECDH 后 HKDF 输出必须一致
    const js = generateKeyPairJS();
    const wc = await generateKeyPair();

    const keyFromJs = deriveSessionKeyJS(js.privateKey, wc.publicKeyB64);
    const keyFromWc = await deriveSessionKey(wc.privateKey, js.publicKeyB64);

    expect(keyFromJs).toEqual(keyFromWc);
    expect(keyFromJs).toHaveLength(32);
  });

  it('ChaCha20-Poly1305 加密解密互逆', async () => {
    const { privateKey } = await generateKeyPair();
    const peer = await generateKeyPair();
    const sessionKey = await deriveSessionKey(privateKey, peer.publicKeyB64);

    const plaintext = new TextEncoder().encode('Hello, JFLove!');
    const { nonce, ciphertext } = encryptEnvelope(sessionKey, plaintext);
    const decrypted = decryptEnvelope(sessionKey, nonce, ciphertext);

    expect(new TextDecoder().decode(decrypted)).toBe('Hello, JFLove!');
  });

  it('不同 session_key 解密失败', async () => {
    const alice = await generateKeyPair();
    const bob = await generateKeyPair();
    const eve = await generateKeyPair();

    const sessionKey = await deriveSessionKey(alice.privateKey, bob.publicKeyB64);
    const wrongKey = await deriveSessionKey(eve.privateKey, alice.publicKeyB64);

    const plaintext = new TextEncoder().encode('secret');
    const { nonce, ciphertext } = encryptEnvelope(sessionKey, plaintext);

    // 不同密钥解密应失败
    expect(() => decryptEnvelope(wrongKey, nonce, ciphertext)).toThrow();
  });
});
