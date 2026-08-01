/**
 * API 请求/响应辅助类型
 */

/** 加密信封格式（与后端/桌面端/移动端完全一致） */
export interface EncryptedEnvelope {
  nonce: string;   // Base64 编码的 12 字节随机 nonce
  ciphertext: string; // Base64 编码的密文（含 16 字节 Poly1305 tag）
}

/** 通用 API 响应（加密解密后） */
export type ApiResponse<T> = T;

/** 通用错误响应（解密后） */
export interface ApiErrorResponse {
  detail: string;
}
