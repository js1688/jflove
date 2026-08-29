/// StreamProxy 纯 byte 模式单元测试（v1.4.2）
///
/// v1.4.2 移除了 time 修复流（mapRangeToSeconds / seek() / _seekVersion），
/// 本文件验证纯 byte 模式的 URL 格式与 repairTaskId 构造语义。
library;

import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';

import 'package:jflove_app/utils/stream_proxy.dart';

void main() {
  group('StreamProxy v1.4.2 纯 byte 模式', () {
    test('URL 不含 seek 版本号 query（v1.4.2 移除 ?v=）', () {
      final proxy = StreamProxy(
        diskId: 1,
        path: '',
        filename: 'a.mp4',
        sessionKey: Uint8List(32),
        sessionId: 'sid',
        serverUrl: 'http://127.0.0.1:8989',
        jwtToken: 'tok',
      );
      // 未 start 时 port=0，只验证 URL 不带 query
      expect(proxy.url.contains('?'), isFalse);
    });

    test('repairTaskId 默认 0，可显式设置（验证播放）', () {
      final proxy = StreamProxy(
        diskId: 1,
        path: '',
        filename: 'a.mp4',
        sessionKey: Uint8List(32),
        sessionId: 'sid',
        serverUrl: 'http://127.0.0.1:8989',
        jwtToken: 'tok',
      );
      expect(proxy.repairTaskId, 0);
      final verify = StreamProxy(
        diskId: 1,
        path: '',
        filename: 'a.mp4',
        sessionKey: Uint8List(32),
        sessionId: 'sid',
        serverUrl: 'http://127.0.0.1:8989',
        jwtToken: 'tok',
        repairTaskId: 42,
      );
      expect(verify.repairTaskId, 42);
    });
  });
}
