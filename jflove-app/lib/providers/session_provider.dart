import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../services/auth_service.dart';
import '../utils/http_service.dart';
import '../utils/session.dart';

/// Riverpod 全局单例：SessionManager
final sessionManagerProvider = Provider<SessionManager>((ref) {
  return SessionManager();
});

/// HTTP 服务（依赖 session）
final httpServiceProvider = Provider<HttpService>((ref) {
  final session = ref.watch(sessionManagerProvider);
  return HttpService(session);
});

/// 认证服务（依赖 http + session）
final authServiceProvider = Provider<AuthService>((ref) {
  final http = ref.watch(httpServiceProvider);
  final session = ref.watch(sessionManagerProvider);
  return AuthService(http, session);
});
