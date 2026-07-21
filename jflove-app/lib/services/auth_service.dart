import '../models/user.dart';
import '../utils/crypto.dart';
import '../utils/http_service.dart';
import '../utils/session.dart';

/// 认证服务
/// 对标 jflove-desktop/src/services/auth_service.py
class AuthService {
  final HttpService _http;
  final SessionManager _session;

  AuthService(this._http, this._session);

  /// 密钥交换
  Future<void> keyExchange() async {
    final kp = CryptoUtils.generateKeyPair();
    final resp = await _http.plainPost('/api/v1/auth/key-exchange', {
      'client_public_key': kp.publicKeyB64,
    });

    _session.sessionKey = CryptoUtils.deriveSessionKey(
      kp.privateKeyRaw,
      resp['server_public_key'] as String,
    );
    _session.sessionId = resp['session_id'] as String;
    _session.keyExchangeTime = DateTime.now().millisecondsSinceEpoch / 1000;
  }

  /// 检查管理员是否存在
  Future<bool> adminExists() async {
    final resp = await _http.plainGet('/api/v1/auth/admin-exists');
    return resp['exists'] as bool? ?? false;
  }

  /// 注册管理员（首次初始化）
  Future<void> registerAdmin(String username, String password) async {
    await _http.encryptedPost('/api/v1/auth/init-admin', {
      'username': username,
      'password': password,
    });
  }

  /// 登录
  Future<AuthResult> login(
    String username,
    String password, {
    int maxSeconds = 86400,
  }) async {
    final resp = await _http.encryptedPost('/api/v1/auth/login', {
      'username': username,
      'password': password,
      'requested_ttl_seconds': maxSeconds,
    });
    final result = AuthResult.fromJson(resp);
    _session.token = result.token;
    _session.userId = result.userId;
    _session.username = result.username;
    _session.role = result.role;
    _session.tokenExpiresAt = result.expiresAt;
    _session.localSessionMaxSeconds = maxSeconds;
    await _session.saveToStorage();
    return result;
  }

  /// 登出（仅清除本地会话，不调服务端接口）
  Future<void> logout() async {
    await _session.clear();
  }
}
