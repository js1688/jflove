import 'package:flutter_test/flutter_test.dart';

import 'package:jflove_app/models/sync_config.dart';

/// 同步配置模型测试
void main() {
  group('SyncConfig', () {
    test('toJson / fromJson 往返', () {
      final config = SyncConfig(
        id: 'test_001',
        name: '照片备份',
        diskId: 1,
        remotePath: 'photos',
        localPath: '/storage/DCIM',
        autoSync: true,
        syncInterval: 300,
        enabled: true,
        lastSyncedAt: '2026-07-16T10:00:00Z',
      );

      final json = config.toJson();
      final restored = SyncConfig.fromJson(json);

      expect(restored.id, 'test_001');
      expect(restored.name, '照片备份');
      expect(restored.diskId, 1);
      expect(restored.autoSync, true);
      expect(restored.enabled, true);
      expect(restored.lastSyncedAt, '2026-07-16T10:00:00Z');
    });

    test('copyWith 方法', () {
      final config = SyncConfig(
        id: '1',
        name: '默认配置',
        diskId: 1,
        localPath: '/data',
      );

      final updated = config.copyWith(name: '新配置', autoSync: true);
      expect(updated.name, '新配置');
      expect(updated.autoSync, true);
      expect(updated.id, '1'); // 不变
      expect(updated.diskId, 1); // 不变
    });

    test('默认值', () {
      final config = SyncConfig(id: '1', diskId: 1, localPath: '/test');

      expect(config.name, '');
      expect(config.autoSync, false);
      expect(config.enabled, true);
      expect(config.syncInterval, 300);
    });

    test('空白 JSON 默认值', () {
      final config = SyncConfig.fromJson({});
      expect(config.id, '');
      expect(config.name, '');
      expect(config.diskId, 0);
      expect(config.enabled, true);
    });
  });
}
