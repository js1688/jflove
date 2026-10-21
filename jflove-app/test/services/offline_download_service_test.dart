import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:jflove_app/services/offline_download_service.dart';
import 'package:jflove_app/utils/http_service.dart';
import 'package:jflove_app/utils/session.dart';

/// 记录调用参数的假 HTTP 层（**不发真实请求**）
///
/// 只重写 `encryptedGet`：只读服务如果调用了任何写方法（`encryptedPost` …）
/// 会直接命中基类实现，测试用 `writeCalls` 计数兜底断言。
class _RecordingHttpService extends HttpService {
  _RecordingHttpService() : super(SessionManager());

  /// (path, body) 调用记录
  final List<(String, Map<String, dynamic>?)> getCalls = [];
  int writeCalls = 0;

  Map<String, dynamic> response = const <String, dynamic>{};

  @override
  Future<Map<String, dynamic>> encryptedGet(
    String path,
    Map<String, dynamic>? data,
  ) async {
    getCalls.add((path, data));
    return response;
  }

  @override
  Future<Map<String, dynamic>> encryptedPost(
    String path,
    Map<String, dynamic>? data,
  ) async {
    writeCalls++;
    return const <String, dynamic>{};
  }
}

/// 离线下载只读服务测试（v1.8.0）
///
/// 目的：锁死「移动端只读」的两条事实 ——
///   ① 只调用 3 个只读接口，路径 / 参数与设计文档 §2.2 完全一致；
///   ② 服务源码里**没有**任何写接口（analyze/select/cancel/retry/...）。
void main() {
  group('OfflineDownloadService', () {
    late _RecordingHttpService http;
    late OfflineDownloadService service;

    setUp(() {
      http = _RecordingHttpService();
      service = OfflineDownloadService(http);
    });

    test('tasks：GET /api/v1/offline/tasks，body 只带 page/page_size', () async {
      http.response = {
        'total': 1,
        'tasks': [
          {'id': 1, 'page_title': '产物 A', 'status': 'success'},
        ],
      };

      final list = await service.tasks(page: 2, pageSize: 50);

      expect(http.writeCalls, 0, reason: '只读服务不得调用写接口');
      expect(http.getCalls.single.$1, '/api/v1/offline/tasks');
      expect(http.getCalls.single.$2, {'page': 2, 'page_size': 50});
      expect(list.total, 1);
      expect(list.tasks.single.productName, '产物 A');
    });

    test('detail：GET /api/v1/offline/detail，task_id 走加密 body', () async {
      http.response = {
        'task': {'id': 9, 'status': 'failed', 'error_message': '磁盘剩余空间不足'},
        'items': [
          {'id': 11, 'kind': 'video', 'display_name': '片段'},
        ],
        'has_more_images': false,
      };

      final detail = await service.detail(9);

      expect(http.writeCalls, 0);
      expect(http.getCalls.single.$1, '/api/v1/offline/detail');
      expect(http.getCalls.single.$2, {'task_id': 9});
      expect(detail.task.errorMessage, '磁盘剩余空间不足');
      expect(detail.items.single.displayName, '片段');
      expect(detail.hasMoreImages, isFalse);
    });

    test('meta：GET /api/v1/offline/meta，附加参数为 null（仅 token）', () async {
      http.response = {
        'enabled': true,
        'can_use': false,
        'unusable_reason': 'no_writable_disk',
        'writable_disks': <Map<String, dynamic>>[],
      };

      final meta = await service.meta();

      expect(http.writeCalls, 0);
      expect(http.getCalls.single.$1, '/api/v1/offline/meta');
      expect(http.getCalls.single.$2, isNull);
      expect(meta.canUse, isFalse);
      expect(meta.unusableReason, 'no_writable_disk');
    });

    test('服务源码只含 3 个只读 GET，不含任何写接口路径', () {
      final source = File(
        'lib/services/offline_download_service.dart',
      ).readAsStringSync();

      // 前缀与 3 个只读路径
      expect(source.contains("'/api/v1/offline'"), isTrue);
      expect(source.contains(r'$_prefix/tasks'), isTrue);
      expect(source.contains(r'$_prefix/detail'), isTrue);
      expect(source.contains(r'$_prefix/meta'), isTrue);
      expect(RegExp(r'encryptedGet\(').allMatches(source).length, 3);

      // 不得出现写方法或写接口路径（AC-231：移动端只读）
      expect(source.contains('encryptedPost'), isFalse);
      expect(source.contains('encryptedPut'), isFalse);
      expect(source.contains('encryptedDelete'), isFalse);
      for (final forbidden in const [
        "'/analyze'",
        "'/select'",
        "'/next-page'",
        "'/cancel'",
        "'/retry'",
        "'/delete-record'",
        "'/settings'",
      ]) {
        expect(
          source.contains(forbidden),
          isFalse,
          reason: '移动端只读服务不得包含写接口 $forbidden',
        );
      }
    });
  });
}
