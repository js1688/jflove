/**
 * 加密工具模块
 *
 * 对标桌面端 src/utils/crypto.py 和移动端 lib/utils/crypto.dart。
 * 使用 Web Crypto API (X25519 ECDH + HKDF-SHA256) + @noble/ciphers (ChaCha20-Poly1305)。
 *
 * 加密协议三端统一：
 *   - 密钥交换：X25519 ECDH
 *   - 密钥派生：HKDF-SHA256（盐 b"jflove-v1"，32B）
 *   - 数据加密：ChaCha20-Poly1305（12B 随机 nonce）
 *   - 加密信封：{"nonce": "<Base64>", "ciphertext": "<Base64>"}
 *   - 流式帧：  [4B 大端长度][12B nonce][密文+16B Poly1305 tag]
 */

import { chacha20poly1305 } from '@noble/ciphers/chacha';
import { SESSION_KEY_SALT, SESSION_KEY_LENGTH, NONCE_LENGTH } from '../config/constants';

// ── X25519 密钥生成 ───────────────────────────

/** 生成 X25519 临时密钥对 */
export async function generateKeyPair(): Promise<{
  publicKeyB64: string;
  privateKey: CryptoKey;
}> {
  const keyPair = await crypto.subtle.generateKey(
    { name: 'X25519' },
    true,
    ['deriveBits'],
  ) as CryptoKeyPair;
  const publicKeyRaw = await crypto.subtle.exportKey('raw', keyPair.publicKey);
  const publicKeyB64 = arrayBufferToBase64(publicKeyRaw);
  return { publicKeyB64, privateKey: keyPair.privateKey };
}

// ── ECDH + HKDF 派生 session_key ──────────────

/** ECDH + HKDF-SHA256 派生 32 字节会话密钥 */
export async function deriveSessionKey(
  privateKey: CryptoKey,
  peerPublicKeyB64: string,
): Promise<Uint8Array> {
  const peerPublicKeyRaw = base64ToUint8Array(peerPublicKeyB64);

  const peerPublicKey = await crypto.subtle.importKey(
    'raw',
    peerPublicKeyRaw.buffer as ArrayBuffer,
    { name: 'X25519' },
    false,
    [],
  );

  // ECDH 共享密钥
  const sharedSecret = await crypto.subtle.deriveBits(
    {
      name: 'X25519',
      public: peerPublicKey,
    },
    privateKey,
    256, // X25519 输出 256 bits
  );

  // HKDF-SHA256 派生 32 字节 session_key
  const hkdfKey = await crypto.subtle.importKey(
    'raw',
    new Uint8Array(sharedSecret),
    { name: 'HKDF' },
    false,
    ['deriveBits'],
  );

  const sessionKey = await crypto.subtle.deriveBits(
    {
      name: 'HKDF',
      hash: 'SHA-256',
      salt: SESSION_KEY_SALT,
      info: new Uint8Array(0),
    },
    hkdfKey,
    SESSION_KEY_LENGTH * 8,
  );

  return new Uint8Array(sessionKey);
}

// ── ChaCha20-Poly1305 加密/解密 ───────────────

/** ChaCha20-Poly1305 加密 */
export function encryptEnvelope(
  sessionKey: Uint8Array,
  plaintext: Uint8Array,
): { nonce: string; ciphertext: string } {
  const nonce = crypto.getRandomValues(new Uint8Array(NONCE_LENGTH));
  const chacha = chacha20poly1305(sessionKey, nonce);
  const ciphertext = chacha.encrypt(plaintext);
  return {
    nonce: arrayBufferToBase64(nonce),
    ciphertext: arrayBufferToBase64(ciphertext),
  };
}

/** ChaCha20-Poly1305 解密 */
export function decryptEnvelope(
  sessionKey: Uint8Array,
  nonceB64: string,
  ciphertextB64: string,
): Uint8Array {
  const nonce = base64ToUint8Array(nonceB64);
  const ciphertext = base64ToUint8Array(ciphertextB64);
  const chacha = chacha20poly1305(sessionKey, nonce);
  return chacha.decrypt(ciphertext);
}

// ── 流式帧加密/解密 ─────────────────────────

/**
 * 加密流式帧。
 * 帧格式：[4B 大端长度][12B nonce][密文+16B Poly1305 tag]
 * 长度 = 12 + plaintext.length + 16（nonce + ciphertext + tag）
 */
export function encryptStreamChunk(
  sessionKey: Uint8Array,
  plaintext: Uint8Array,
): Uint8Array {
  const nonce = crypto.getRandomValues(new Uint8Array(NONCE_LENGTH));
  const chacha = chacha20poly1305(sessionKey, nonce);
  const ciphertext = chacha.encrypt(plaintext);

  // 帧总长度 = 4B 长度 + 12B nonce + ciphertext
  const frameLength = NONCE_LENGTH + ciphertext.length;
  const result = new Uint8Array(4 + frameLength);

  // 写入 4 字节大端长度
  const view = new DataView(result.buffer);
  view.setUint32(0, frameLength, false); // big-endian

  result.set(nonce, 4);
  result.set(ciphertext, 4 + NONCE_LENGTH);

  return result;
}

/** 解密流式帧 */
export function decryptStreamChunk(
  sessionKey: Uint8Array,
  frameBody: Uint8Array,
): Uint8Array {
  const nonce = frameBody.slice(0, NONCE_LENGTH);
  const ciphertext = frameBody.slice(NONCE_LENGTH);
  const chacha = chacha20poly1305(sessionKey, nonce);
  return chacha.decrypt(ciphertext);
}

// ── Base64 / Uint8Array 转换 ────────────────

export function arrayBufferToBase64(buffer: ArrayBuffer | Uint8Array): string {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

export function base64ToUint8Array(b64: string): Uint8Array {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

// ── BufferSource → Uint8Array ───────────────

export function cryptoKeyToUint8Array(key: ArrayBuffer): Uint8Array {
  return new Uint8Array(key);
}
