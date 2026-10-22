import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:jflove_app/services/offline_download_service.dart';
import 'package:jflove_app/utils/http_service.dart';
import 'package:jflove_app/utils/session.dart';

/// 记录调用参数的假 HTTP 层（**不发真实请求**）
class _RecordingHttpService extends HttpService {
  _RecordingHttpService() : super(SessionManager());

  /// (method, path, body) 调用记录
  final List<(String, String, Map<String, dynamic>?)> calls = [];

  Map<String, dynamic> response = const <String, dynamic>{};

  @override
  Future<Map<String, dynamic>> encryptedGet(
    String path,
    Map<String, dynamic>? data,
  ) async {
    calls.add(('GET', path, data));
    return response;
  }

  @override
  Future<Map<String, dynamic>> encryptedPost(
    String path,
    Map<String, dynamic>? data,
  ) async {
    calls.add(('POST', path, data));
    return response;
  }

  @override
  Future<Map<String, dynamic>> encryptedPut(
    String path,
    Map<String, dynamic>? data,
  ) async {
    calls.add(('PUT', path, data));
    return response;
  }

  @override
  Future<Map<String, dynamic>> encryptedDelete(
    String path,
    Map<String, dynamic>? data,
  ) async {
    calls.add(('DELETE', path, data));
    return response;
  }
}

/// 离线下载服务测试（v1.8.1：移动端完整功能）
///
/// 锁死 7 个写接口的 method / path / 字段与 controller 完全一致；
/// 所有业务字段走加密 body，不进 URL。
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

      expect(http.calls.single.$1, 'GET');
      expect(http.calls.single.$2, '/api/v1/offline/tasks');
      expect(http.calls.single.$3, {'page': 2, 'page_size': 50});
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

      expect(http.calls.single.$1, 'GET');
      expect(http.calls.single.$2, '/api/v1/offline/detail');
      expect(http.calls.single.$3, {'task_id': 9});
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

      expect(http.calls.single.$1, 'GET');
      expect(http.calls.single.$2, '/api/v1/offline/meta');
      expect(http.calls.single.$3, isNull);
      expect(meta.canUse, isFalse);
      expect(meta.unusableReason, 'no_writable_disk');
    });

    test('analyze：POST /analyze，url 走加密 body', () async {
      http.response = {
        'task_id': 12,
        'status': 'analyzing',
        'source_domain': 'example.com',
      };

      final result = await service.analyze('https://example.com/v');

      expect(http.calls.single.$1, 'POST');
      expect(http.calls.single.$2, '/api/v1/offline/analyze');
      expect(http.calls.single.$3, {'url': 'https://example.com/v'});
      expect(result.taskId, 12);
      expect(result.status, 'analyzing');
    });

    test('select：POST /select，字段与 controller 完全一致', () async {
      http.response = {
        'task_id': 12,
        'status': 'downloading',
        'selected': 2,
        'bytes_total': 2048,
      };

      final result = await service.select(
        taskId: 12,
        itemIds: const [3, 4],
        diskId: 1,
        targetPath: 'downloads/2026',
        packZip: true,
      );

      expect(http.calls.single.$1, 'POST');
      expect(http.calls.single.$2, '/api/v1/offline/select');
      expect(http.calls.single.$3, {
        'task_id': 12,
        'item_ids': [3, 4],
        'disk_id': 1,
        'target_path': 'downloads/2026',
        'pack_zip': true,
      });
      expect(result.selected, 2);
      expect(result.bytesTotal, 2048);
    });

    test('nextPage：POST /next-page', () async {
      http.response = {
        'added': 5,
        'item_total': 20,
        'has_more': true,
        'page_index': 2,
      };

      final result = await service.nextPage(12);

      expect(http.calls.single.$1, 'POST');
      expect(http.calls.single.$2, '/api/v1/offline/next-page');
      expect(http.calls.single.$3, {'task_id': 12});
      expect(result.added, 5);
      expect(result.hasMore, isTrue);
    });

    test('cancel / retry / deleteRecord：POST + task_id', () async {
      http.response = {'task_id': 12, 'status': 'canceled'};
      await service.cancel(12);
      http.response = {'task_id': 12, 'status': 'downloading'};
      await service.retry(12);
      http.response = {'task_id': 12, 'deleted': true};
      final deleted = await service.deleteRecord(12);

      expect(http.calls.map((c) => c.$2).toList(), [
        '/api/v1/offline/cancel',
        '/api/v1/offline/retry',
        '/api/v1/offline/delete-record',
      ]);
      expect(http.calls.every((c) => c.$1 == 'POST'), isTrue);
      expect(http.calls.every((c) => c.$3!.containsKey('task_id')), isTrue);
      expect(deleted.deleted, isTrue);
    });

    test('saveSettings：POST /settings，disk_id/rel_path/create', () async {
      http.response = {
        'configured': true,
        'disk_id': 1,
        'rel_path': 'downloads',
      };

      final result = await service.saveSettings(
        diskId: 1,
        relPath: 'downloads',
        create: true,
      );

      expect(http.calls.single.$1, 'POST');
      expect(http.calls.single.$2, '/api/v1/offline/settings');
      expect(http.calls.single.$3, {
        'disk_id': 1,
        'rel_path': 'downloads',
        'create': true,
      });
      expect(result.configured, isTrue);
      expect(result.diskId, 1);
    });

    test('服务源码不直接使用 dio / 明文 URL 业务参数', () {
      final source = File(
        'lib/services/offline_download_service.dart',
      ).readAsStringSync();

      expect(source.contains("import 'package:dio/dio.dart'"), isFalse);
      // 所有业务参数都在加密 body（encryptedGet/Post）里
      expect(source.contains('encryptedPost'), isTrue);
      expect(source.contains('encryptedGet'), isTrue);
      expect(source.contains(r'$_prefix/analyze'), isTrue);
      expect(source.contains(r'$_prefix/delete-record'), isTrue);
    });
  });
}
