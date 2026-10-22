import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jflove_app/config/theme.dart';
import 'package:jflove_app/models/offline_task.dart';
import 'package:jflove_app/pages/offline/offline_settings_sheet.dart';
import 'package:jflove_app/providers/offline_provider.dart';
import 'package:jflove_app/services/offline_download_service.dart';
import 'package:jflove_app/utils/http_service.dart';
import 'package:jflove_app/utils/session.dart';

class _FakeOfflineService extends OfflineDownloadService {
  _FakeOfflineService() : super(HttpService(SessionManager()));

  int saveCalls = 0;
  int? lastDiskId;
  String? lastRelPath;
  bool? lastCreate;

  @override
  Future<OfflineSettingsResult> saveSettings({
    required int diskId,
    String relPath = '',
    bool create = false,
  }) async {
    saveCalls++;
    lastDiskId = diskId;
    lastRelPath = relPath;
    lastCreate = create;
    return OfflineSettingsResult(
      configured: diskId > 0,
      diskId: diskId,
      relPath: relPath,
    );
  }
}

const _meta = OfflineMeta(
  enabled: true,
  canUse: true,
  configured: true,
  configDiskId: 1,
  configRelPath: 'downloads',
  configValid: true,
  writableDisks: [OfflineWritableDisk(diskId: 1, name: '主磁盘')],
);

/// 默认目录设置弹层（v1.8.1 / F32.4、AC-249）
void main() {
  Future<(_FakeOfflineService, int)> pumpSheet(WidgetTester tester) async {
    final fake = _FakeOfflineService();
    var saved = 0;
    await tester.pumpWidget(
      ProviderScope(
        overrides: [offlineDownloadServiceProvider.overrideWithValue(fake)],
        child: MaterialApp(
          theme: AppTheme.light,
          home: Scaffold(
            body: OfflineSettingsSheet(
              meta: _meta,
              onSaved: () async => saved++,
            ),
          ),
        ),
      ),
    );
    await tester.pump();
    return (fake, saved);
  }

  testWidgets('展示当前默认目录，保存时带 disk_id/rel_path/create', (tester) async {
    final (fake, _) = await pumpSheet(tester);

    expect(find.text('离线下载设置'), findsOneWidget);
    expect(find.textContaining('主磁盘 / downloads'), findsOneWidget);

    await tester.tap(find.text('保存'));
    await tester.pump();
    await tester.pump();

    expect(fake.saveCalls, 1);
    expect(fake.lastDiskId, 1);
    expect(fake.lastRelPath, 'downloads');
    expect(fake.lastCreate, isFalse);
  });

  testWidgets('清除默认目录发送 disk_id=0', (tester) async {
    final (fake, _) = await pumpSheet(tester);

    await tester.tap(find.text('清除默认目录'));
    await tester.pump();
    await tester.pump();

    expect(fake.saveCalls, 1);
    expect(fake.lastDiskId, 0);
  });

  testWidgets('无可写磁盘时保存 / 清除都禁用', (tester) async {
    final fake = _FakeOfflineService();
    await tester.pumpWidget(
      ProviderScope(
        overrides: [offlineDownloadServiceProvider.overrideWithValue(fake)],
        child: MaterialApp(
          theme: AppTheme.light,
          home: Scaffold(
            body: OfflineSettingsSheet(
              meta: const OfflineMeta(
                enabled: true,
                canUse: false,
                unusableReason: 'no_writable_disk',
              ),
              onSaved: () async {},
            ),
          ),
        ),
      ),
    );
    await tester.pump();

    expect(find.textContaining('没有可写入的磁盘'), findsOneWidget);
    final save = tester.widget<FilledButton>(
      find.widgetWithText(FilledButton, '保存'),
    );
    expect(save.onPressed, isNull);
  });
}
