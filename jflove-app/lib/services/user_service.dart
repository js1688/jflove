import '../models/user.dart';
import '../utils/http_service.dart';

/// 用户管理服务
/// 对标 jflove-desktop/src/services/user_service.py
class UserService {
  final HttpService _http;

  UserService(this._http);

  /// 用户列表
  /// 对应服务端：GET /api/v1/users（需 admin 权限）
  Future<List<User>> listUsers() async {
    final resp = await _http.encryptedGet('/api/v1/users', {});
    final items = resp['users'] as List<dynamic>;
    return items.map((e) => User.fromJson(e as Map<String, dynamic>)).toList();
  }

  /// 创建用户
  /// 对应服务端：POST /api/v1/users（需 admin 权限）
  Future<void> createUser(String username, String password) async {
    await _http.encryptedPost('/api/v1/users', {
      'username': username,
      'password': password,
    });
  }

  /// 修改密码
  Future<void> changePassword(int userId, String newPassword) async {
    await _http.encryptedPut('/api/v1/users/$userId/password', {
      'password': newPassword,
    });
  }

  /// 启用/禁用用户
  Future<void> setEnabled(int userId, bool enabled) async {
    await _http.encryptedPut('/api/v1/users/$userId/enabled', {
      'enabled': enabled,
    });
  }

  /// 删除用户
  Future<void> deleteUser(int userId) async {
    await _http.encryptedDelete('/api/v1/users/$userId', null);
  }
}
