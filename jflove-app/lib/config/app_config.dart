/// JFLove 移动端应用配置
class AppConfig {
  AppConfig._();

  /// 加密盐（与 jflove-server 一致）
  static const String cryptoSalt = 'jflove-v1';

  /// session_key 派生长度（32 字节）
  static const int sessionKeyLength = 32;

  /// ChaCha20-Poly1305 nonce 长度（12 字节）
  static const int nonceLength = 12;

  /// 流式分片明文大小（64 KB，与服务端一致）
  static const int streamPlaintextChunkSize = 64 * 1024;

  /// 请求超时（秒）
  static const int connectTimeout = 15;
  static const int receiveTimeout = 30;

  /// 最大重试次数
  static const int maxRetries = 3;

  /// 文件上传分片大小（1 MB）
  static const int uploadChunkSize = 1024 * 1024;

  /// 明文白名单路径
  static const List<String> plainPaths = [
    '/health',
    '/api/v1/auth/key-exchange',
    '/api/v1/auth/admin-exists',
  ];
}
