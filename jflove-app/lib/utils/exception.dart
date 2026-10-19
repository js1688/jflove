import 'package:dio/dio.dart';

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

/// 将任意异常转换为可直接展示给用户的中文文案。
///
/// 传输任务与相册备份不直接展示 `AppException(...)` / Dio 原始英文信息，
/// 统一在这里收敛，避免英文异常原文泄漏到界面（v1.7.0 AC-128）。
String userFacingError(Object error, {String fallback = '操作失败，请稍后重试'}) {
  if (error is AppException) {
    final message = error.message.trim();
    return message.isEmpty ? fallback : message;
  }
  if (error is DioException) {
    switch (error.type) {
      case DioExceptionType.connectionTimeout:
      case DioExceptionType.sendTimeout:
      case DioExceptionType.receiveTimeout:
      case DioExceptionType.transformTimeout:
        return '连接超时，请检查网络或服务器地址';
      case DioExceptionType.connectionError:
        return '无法连接服务器，请检查网络或服务器地址';
      case DioExceptionType.badCertificate:
        return '服务器证书校验失败';
      case DioExceptionType.cancel:
        return '请求已取消';
      case DioExceptionType.badResponse:
        return '服务器返回异常（${error.response?.statusCode ?? '未知状态'}）';
      case DioExceptionType.unknown:
        return '网络请求失败，请检查网络或服务器地址';
    }
  }
  return fallback;
}
