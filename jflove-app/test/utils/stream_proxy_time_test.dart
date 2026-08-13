/// StreamProxy time 修复流线性映射单元测试（v1.4.0 方案 B）
///
/// 覆盖：字节偏移 → 时间秒的线性映射、零值/边界保护。
library;

import 'package:flutter_test/flutter_test.dart';

import 'package:jflove_app/utils/stream_proxy.dart';

void main() {
  group('mapRangeToSeconds 线性映射', () {
    test('中点偏移映射为时长一半', () {
      // file_size=10000, duration=10s → 偏移 5000 → 5.0s
      final seconds = StreamProxy.mapRangeToSeconds(5000, 10000, 10.0);
      expect(seconds, closeTo(5.0, 1e-9));
    });

    test('零偏移从零开始', () {
      expect(StreamProxy.mapRangeToSeconds(0, 10000, 10.0), 0.0);
    });

    test('fileSize 为零时防除零返回零', () {
      expect(StreamProxy.mapRangeToSeconds(5000, 0, 10.0), 0.0);
    });

    test('duration 为零时返回零', () {
      expect(StreamProxy.mapRangeToSeconds(5000, 10000, 0.0), 0.0);
    });

    test('负偏移返回零', () {
      expect(StreamProxy.mapRangeToSeconds(-100, 10000, 10.0), 0.0);
    });
  });
}
