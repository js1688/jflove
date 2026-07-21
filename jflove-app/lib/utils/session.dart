import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// 会话管理器（单例模式）
///
/// 对标 jflove-desktop/src/utils/session.py。
/// session_key 仅存内存，token 存 Android Keystore。
class SessionManager {
  SessionManager._();

  static final SessionManager _instance = SessionManager._();
  factory SessionManager() => _instance;

  final FlutterSecureStorage _storage = const FlutterSecureStorage();

  // ---- 内存字段（不持久化） ----

  /// 服务端 URL
  String serverUrl = '';

  /// 当前会话 ID（key-exchange 返回）
  String sessionId = '';

  /// 32 字节会话密钥（仅内存，不落盘）
  Uint8List? sessionKey;

  /// 密钥交换时间戳
  double keyExchangeTime = 0;

  // ---- 持久化字段（通过 flutter_secure_storage） ----

  String token = '';
  int? userId;
  String username = '';
  String role = '';
  double tokenExpiresAt = 0; // Unix 秒
  int localSessionMaxSeconds = 0;
  List<String> serverHistory = [];

  // ---- 计算属性 ----

  bool get isSessionReady => sessionId.isNotEmpty && sessionKey != null;

  bool get isLoggedIn => token.isNotEmpty && isSessionReady;

  bool get isAdmin => role == 'admin';

  /// 登录有效期截止时间
  double get effectiveExpireAt {
    final tokenExp = tokenExpiresAt;
    if (tokenExp <= 0) return double.infinity;
    return tokenExp;
  }

  /// 保存持久化字段到安全存储
  Future<void> saveToStorage() async {
    final data = {
      'token': token,
      'userId': userId?.toString() ?? '',
      'username': username,
      'role': role,
      'tokenExpiresAt': tokenExpiresAt.toString(),
      'localSessionMaxSeconds': localSessionMaxSeconds.toString(),
      'serverHistory': jsonEncode(serverHistory),
      'serverUrl': serverUrl,
    };
    for (final entry in data.entries) {
      await _storage.write(key: entry.key, value: entry.value);
    }
  }

  /// 从安全存储加载持久化字段
  Future<void> loadFromStorage() async {
    token = await _storage.read(key: 'token') ?? '';
    userId = int.tryParse(await _storage.read(key: 'userId') ?? '');
    username = await _storage.read(key: 'username') ?? '';
    role = await _storage.read(key: 'role') ?? '';
    tokenExpiresAt = double.tryParse(await _storage.read(key: 'tokenExpiresAt') ?? '0') ?? 0;
    localSessionMaxSeconds = int.tryParse(await _storage.read(key: 'localSessionMaxSeconds') ?? '0') ?? 0;
    serverUrl = await _storage.read(key: 'serverUrl') ?? '';

    final historyStr = await _storage.read(key: 'serverHistory');
    if (historyStr != null && historyStr.isNotEmpty) {
      final list = jsonDecode(historyStr);
      if (list is List) {
        serverHistory = list.cast<String>();
      }
    }
  }

  /// 清除所有会话数据（退出登录）
  Future<void> clear() async {
    token = '';
    userId = null;
    username = '';
    role = '';
    tokenExpiresAt = 0;
    sessionId = '';
    sessionKey = null;
    keyExchangeTime = 0;
    await _storage.deleteAll();
  }

  /// 转字典（用于调试，不包含 session_key）
  Map<String, dynamic> toDict() => {
        'serverUrl': serverUrl,
        'sessionId': sessionId,
        'hasSessionKey': sessionKey != null,
        'token': token.isNotEmpty ? '***${token.substring(max(0, token.length - 8))}' : '',
        'userId': userId,
        'username': username,
        'role': role,
        'tokenExpiresAt': tokenExpiresAt,
        'localSessionMaxSeconds': localSessionMaxSeconds,
        'serverHistory': serverHistory,
      };
}
