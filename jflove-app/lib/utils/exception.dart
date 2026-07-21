/// JFLove 移动端自定义异常
class AppException implements Exception {
  final int code;
  final String message;

  const AppException({
    required this.code,
    required this.message,
  });

  @override
  String toString() => 'AppException(code=$code, message=$message)';

  /// 401 - 未认证
  factory AppException.unauthorized([String? msg]) =>
      AppException(code: 401, message: msg ?? '登录已过期，请重新登录');

  /// 403 - 无权限
  factory AppException.forbidden([String? msg]) =>
      AppException(code: 403, message: msg ?? '无权访问此资源');

  /// 404 - 资源不存在
  factory AppException.notFound([String? msg]) =>
      AppException(code: 404, message: msg ?? '请求的资源不存在');

  /// 网络错误
  factory AppException.networkError([String? msg]) =>
      AppException(code: -1, message: msg ?? '网络连接失败，请检查网络');

  /// 服务端错误
  factory AppException.serverError([String? msg]) =>
      AppException(code: 500, message: msg ?? '服务器内部错误');
}
