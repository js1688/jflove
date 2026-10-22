import 'package:flutter_test/flutter_test.dart';
import 'package:jflove_app/models/offline_task.dart';

/// 离线下载模型容错测试（v1.8.0 / v1.8.1）
///
/// 设计文档 §5 要求 `fromJson` 容错：**字段缺失不崩**。页面所有展示都建立在
/// 这些模型上，一条脏数据不应让整页白屏，因此这里把"字段缺失 / null /
/// 类型不符"逐项锁死。
void main() {
  group('OfflineTask.fromJson', () {
    test('完整字段解析', () {
      final task = OfflineTask.fromJson({
        'id': 7,
        'username': 'alice',
        'source_domain': 'example.com',
        'page_title': '演示产物',
        'status': 'downloading',
        'status_text': '下载中',
        'item_total': 3,
        'item_done': 1,
        'bytes_total': 1000,
        'bytes_done': 250,
        'speed_bps': 1024,
        'target_disk_name': '主磁盘',
        'target_rel_path': 'downloads/2026',
        'error_code': '',
        'error_message': '',
        'created_at': '2026-10-20T10:00:00+00:00',
        'started_at': '2026-10-20T10:00:05+00:00',
        'finished_at': null,
        'can_operate': true,
      });

      expect(task.id, 7);
      expect(task.username, 'alice');
      expect(task.sourceDomain, 'example.com');
      expect(task.pageTitle, '演示产物');
      expect(task.statusLabel, '下载中');
      expect(task.bytesDone, 250);
      expect(task.speedBps, 1024);
      expect(task.startedAt, isNotNull);
      expect(task.finishedAt, isNull);
      expect(task.canOperate, isTrue);
      expect(task.isTransferring, isTrue);
      expect(task.isTerminal, isFalse);
      expect(task.targetDisplay, '主磁盘/downloads/2026');
      expect(task.progress, closeTo(0.25, 1e-9));
      expect(task.progressPercent, 25);
      expect(task.hasError, isFalse);
    });

    test('空对象 / 全部字段为 null 不崩且回落默认值', () {
      final empty = OfflineTask.fromJson(const {});
      expect(empty.id, 0);
      expect(empty.productName, '未命名产物');
      expect(empty.statusLabel, '');
      expect(empty.progress, 0);
      expect(empty.progressPercent, 0);
      expect(empty.targetDisplay, '—');
      expect(empty.startedAt, isNull);
      expect(empty.isTerminal, isFalse);

      final nulls = OfflineTask.fromJson({
        'id': null,
        'source_domain': null,
        'page_title': null,
        'status_text': null,
        'bytes_total': null,
        'can_operate': null,
        'created_at': null,
      });
      expect(nulls.id, 0);
      expect(nulls.productName, '未命名产物');
      expect(nulls.statusLabel, '');
      expect(nulls.canOperate, isFalse);
    });

    test('类型不符时能转则转，不能转回落默认值（不抛异常）', () {
      final task = OfflineTask.fromJson({
        'id': '9',
        'item_total': '4',
        'bytes_total': '100',
        'bytes_done': 50.0,
        'can_operate': 'true',
        'source_domain': 123,
        'started_at': '',
      });
      expect(task.id, 9);
      expect(task.itemTotal, 4);
      expect(task.bytesTotal, 100);
      expect(task.bytesDone, 50);
      expect(task.canOperate, isTrue);
      expect(task.sourceDomain, '123');
      // 空字符串视为"没有时间"，转成 null
      expect(task.startedAt, isNull);
    });

    test('进度：字节优先，缺字节时用项数，成功恒为 100%', () {
      final byBytes = OfflineTask.fromJson({
        'status': 'downloading',
        'item_total': 10,
        'item_done': 1,
        'bytes_total': 1000,
        'bytes_done': 500,
      });
      expect(byBytes.progressPercent, 50);

      final byItems = OfflineTask.fromJson({
        'status': 'downloading',
        'item_total': 4,
        'item_done': 1,
      });
      expect(byItems.progressPercent, 25);

      final success = OfflineTask.fromJson({'status': 'success'});
      expect(success.progressPercent, 100);
      expect(success.isTerminal, isTrue);

      // 字节越界时按 0~1 收敛，不出现 120%
      final overflow = OfflineTask.fromJson({
        'bytes_total': 100,
        'bytes_done': 300,
      });
      expect(overflow.progressPercent, 100);
    });

    test('目标目录：无磁盘名/无相对路径时的展示兜底', () {
      expect(
        OfflineTask.fromJson({'target_disk_name': '盘A'}).targetDisplay,
        '盘A/',
      );
      expect(
        OfflineTask.fromJson({'target_rel_path': '/a/b/'}).targetDisplay,
        '/a/b',
      );
      expect(OfflineTask.fromJson(const {}).targetDisplay, '—');
    });
  });

  group('OfflineItem.fromJson', () {
    test('完整字段与展示派生属性', () {
      final item = OfflineItem.fromJson({
        'id': 1,
        'kind': 'video',
        'display_name': '片段 1',
        'ext': 'mp4',
        'width': 1920,
        'height': 1080,
        'duration_ms': 65000,
        'est_size': 2048,
        'is_estimated': true,
        'is_selected': true,
        'status': 'success',
        'error_message': '',
      });
      expect(item.title, '片段 1');
      expect(item.resolutionText, '1920x1080');
      expect(item.isEstimated, isTrue);
      expect(item.isSelected, isTrue);
      expect(item.errorMessage, isEmpty);
    });

    test('缺字段 / null 不崩', () {
      final item = OfflineItem.fromJson(null);
      expect(item.id, 0);
      expect(item.title, '未命名项');
      expect(item.resolutionText, '');
      expect(item.isEstimated, isFalse);
      // 宽或高缺一不出分辨率文案
      expect(OfflineItem.fromJson({'width': 100}).resolutionText, '');
    });
  });

  group('列表 / 详情 / 元信息响应', () {
    test('OfflineTaskList：total + tasks，非 Map 元素被跳过', () {
      final list = OfflineTaskList.fromJson({
        'total': '2',
        'tasks': [
          {'id': 1, 'page_title': 'A'},
          'not-a-map',
          {'id': 2, 'page_title': 'B'},
        ],
      });
      expect(list.total, 2);
      expect(list.tasks.length, 2);
      expect(list.tasks.first.productName, 'A');
    });

    test('OfflineTaskDetail：task/items/has_more_images', () {
      final detail = OfflineTaskDetail.fromJson({
        'task': {'id': 5, 'status': 'ready', 'status_text': '待选择'},
        'items': [
          {'id': 1, 'kind': 'image', 'display_name': '图 1'},
        ],
        'has_more_images': true,
      });
      expect(detail.task.id, 5);
      expect(detail.items.single.title, '图 1');
      expect(detail.hasMoreImages, isTrue);
    });

    test('OfflineMeta：config / writable_disks 解析与容错', () {
      final meta = OfflineMeta.fromJson({
        'enabled': true,
        'can_use': true,
        'configured': true,
        'unusable_reason': '',
        'config': {'disk_id': 3, 'rel_path': 'dl', 'valid': true},
        'writable_disks': [
          {'disk_id': 3, 'name': '主磁盘'},
        ],
        'max_items': 200,
        'max_file_size': 1048576,
        'max_images': 100,
        'poll_interval_ms': 2500,
      });
      expect(meta.canUse, isTrue);
      expect(meta.configured, isTrue);
      expect(meta.configDiskId, 3);
      expect(meta.configValid, isTrue);
      expect(meta.writableDisks.single.name, '主磁盘');
      expect(meta.pollIntervalMs, 2500);

      final empty = OfflineMeta.fromJson(const {});
      expect(empty.enabled, isFalse);
      expect(empty.canUse, isFalse);
      expect(empty.unusableReason, '');
      expect(empty.writableDisks, isEmpty);
    });
  });

  group('写接口响应模型（v1.8.1）', () {
    test('OfflineAnalyzeResult：字段缺失 / 类型不符都回落默认值', () {
      final ok = OfflineAnalyzeResult.fromJson({
        'task_id': '12',
        'status': 'analyzing',
        'source_domain': 'example.com',
        'page_title': '页面',
      });
      expect(ok.taskId, 12);
      expect(ok.status, 'analyzing');
      expect(ok.sourceDomain, 'example.com');
      expect(ok.pageTitle, '页面');

      final empty = OfflineAnalyzeResult.fromJson(null);
      expect(empty.taskId, 0);
      expect(empty.status, '');
    });

    test('OfflineSelectResult / OfflineTaskAction 解析', () {
      final selected = OfflineSelectResult.fromJson({
        'task_id': 12,
        'status': 'downloading',
        'selected': 3,
        'bytes_total': 999,
      });
      expect(selected.selected, 3);
      expect(selected.bytesTotal, 999);

      final deleted = OfflineTaskAction.fromJson({
        'task_id': 12,
        'deleted': true,
      });
      expect(deleted.taskId, 12);
      expect(deleted.deleted, isTrue);
      expect(deleted.status, '');
    });

    test('OfflineNextPageResult / OfflineSettingsResult 解析', () {
      final next = OfflineNextPageResult.fromJson({
        'added': 5,
        'item_total': 20,
        'has_more': true,
        'page_index': 2,
      });
      expect(next.added, 5);
      expect(next.itemTotal, 20);
      expect(next.hasMore, isTrue);
      expect(next.pageIndex, 2);

      final settings = OfflineSettingsResult.fromJson({
        'configured': true,
        'disk_id': 3,
        'rel_path': 'dl',
      });
      expect(settings.configured, isTrue);
      expect(settings.diskId, 3);
      expect(settings.relPath, 'dl');
    });
  });

}
