import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jflove_app/utils/exception.dart';

DioException _dioError(DioExceptionType type) => DioException(
  requestOptions: RequestOptions(path: '/api/v1/files/upload/init'),
  type: type,
);

void main() {
  group('userFacingError', () {
    test('AppException 只暴露 message，不暴露 AppException(...) 包装', () {
      final message = userFacingError(
        const AppException(code: -1, message: '无法连接服务器，请检查网络'),
      );
      expect(message, '无法连接服务器，请检查网络');
      expect(message.contains('AppException'), isFalse);
    });

    test('Dio 连接错误转换为中文文案', () {
      expect(
        userFacingError(_dioError(DioExceptionType.connectionError)),
        '无法连接服务器，请检查网络或服务器地址',
      );
    });

    test('Dio 超时转换为中文文案', () {
      expect(
        userFacingError(_dioError(DioExceptionType.receiveTimeout)),
        '连接超时，请检查网络或服务器地址',
      );
    });

    test('未知异常使用调用方给的兜底文案', () {
      expect(
        userFacingError(StateError('boom'), fallback: '上传失败，请稍后重试'),
        '上传失败，请稍后重试',
      );
    });

    test('AppException 空消息回落到兜底文案', () {
      expect(
        userFacingError(
          const AppException(code: -1, message: '   '),
          fallback: '操作失败，请稍后重试',
        ),
        '操作失败，请稍后重试',
      );
    });
  });
}
