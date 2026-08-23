/**
 * 全局配置常量
 *
 * 对标桌面端 src/config/settings.py
 */

/** 应用名称 */
export const APP_NAME = 'JFLove';

/** 版本号 */
export const APP_VERSION = '1.4.1';

/** 默认服务端地址 */
export const DEFAULT_SERVER_URL = 'http://localhost:8989';

/** 分片上传每片大小（1 MB） */
export const CHUNK_SIZE = 1048576;

/** 流式传输明文分片大小（64 KB） */
export const STREAM_PLAINTEXT_CHUNK_SIZE = 65536;

/** HKDF 派生盐值，必须与后端一致 */
export const SESSION_KEY_SALT = new TextEncoder().encode('jflove-v1');

/** 会话密钥长度（字节） */
export const SESSION_KEY_LENGTH = 32;

/** Nonce 长度（字节） */
export const NONCE_LENGTH = 12;

/** 登录有效期选项（秒） */
export const SESSION_TTL_OPTIONS = [
  { value: 86400, label: '1 天' },
  { value: 604800, label: '7 天' },
  { value: 2592000, label: '30 天' },
];

/** 默认登录有效期（30 天） */
export const SESSION_TTL_DEFAULT = 2592000;

/** 服务端地址历史最大条数 */
export const MAX_SERVER_HISTORY = 10;

/** HTTP 请求超时（毫秒） */
export const REQUEST_TIMEOUT_MS = 30000;

/** 流式连接超时（毫秒） */
export const STREAM_TIMEOUT_MS = 60000;

/** 自动保存间隔（毫秒） */
export const AUTO_SAVE_INTERVAL_MS = 30000;

/** 明文接口白名单（无需加密信封） */
export const PLAIN_PATHS = [
  '/health',
  '/api/v1/auth/key-exchange',
  '/api/v1/auth/admin-exists',
];

/** ECDH 续约触发条件（401 detail 包含这些关键词） */
export const ECDH_401_PATTERNS = [
  '会话不存在或已过期',
  '会话已失效',
  '缺少 X-Session-ID',
];
