import 'package:flutter_test/flutter_test.dart';

/// 认证服务测试
///
/// 注意：这些是集成测试，需要运行中的 jflove-server。
/// 在 CI/本地测试前请确保 server 已启动。
void main() {
  group('AuthService', () {
    // TODO: 使用 mock 替换真实 HTTP 请求
    test('AuthService 构造函数', () {
      // 需要 mock HttpService 并注入
      // 暂时跳过，等待 HttpService mock 框架搭建
    });
  });
}
