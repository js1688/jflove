/// 用户数据模型
class User {
  final int id;
  final String username;
  final String role;
  final bool enabled;

  const User({
    required this.id,
    required this.username,
    required this.role,
    required this.enabled,
  });

  factory User.fromJson(Map<String, dynamic> json) => User(
    id: json['id'] as int,
    username: json['username'] as String,
    role: json['role'] as String? ?? 'user',
    enabled: json['enabled'] as bool? ?? true,
  );
}

/// 登录结果
class AuthResult {
  final String token;
  final int userId;
  final String username;
  final String role;
  final double expiresAt;

  const AuthResult({
    required this.token,
    required this.userId,
    required this.username,
    required this.role,
    required this.expiresAt,
  });

  factory AuthResult.fromJson(Map<String, dynamic> json) {
    final expiresIn = (json['expires_in'] as num?)?.toDouble() ?? 3600;
    return AuthResult(
      token: json['token'] as String,
      userId: json['user_id'] as int,
      username: json['username'] as String,
      role: json['role'] as String? ?? 'user',
      expiresAt: DateTime.now().millisecondsSinceEpoch / 1000 + expiresIn,
    );
  }
}
