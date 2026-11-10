import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jflove_app/pages/admin/admin_system_page.dart';
import 'package:jflove_app/services/config_service.dart';
import 'package:jflove_app/utils/http_service.dart';
import 'package:jflove_app/utils/session.dart';

/// 假配置服务：记录写入，永不出网
class _FakeConfigService extends ConfigService {
  _FakeConfigService({this.config = const <String, String>{}})
      : super(HttpService(SessionManager()));

  final Map<String, String> config;
  final List<MapEntry<String, String>> updates = [];

  @override
  Future<Map<String, dynamic>> getConfig() async => {'config': config};

  @override
  Future<Map<String, dynamic>> updateConfig(String key, String value) async {
    updates.add(MapEntry(key, value));
    return {'message': '配置已更新'};
  }
}

/// 离线下载全局策略卡片测试（v1.8.0 / UAT 问题 #1 / AC-247）
///
/// 锁死：默认开启、总开关写入 `offline_download_enabled=0`、
/// 数字项保存逐项写入 4 个配置键。
Future<void> _pumpCard(WidgetTester tester, ConfigService service) async {
  await tester.pumpWidget(
    ProviderScope(
      child: MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: OfflineDownloadConfigCard(configService: service),
          ),
        ),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

SwitchListTile _switchTile(WidgetTester tester, String title) {
  return tester.widget<SwitchListTile>(
    find.widgetWithText(SwitchListTile, title),
  );
}

/// 存储与临时文件卡片（v1.9.1 精简为 3 项）
Future<void> _pumpStorageCard(WidgetTester tester, ConfigService service) async {
  await tester.pumpWidget(
    ProviderScope(
      child: MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: UploadCleanupConfigCard(configService: service),
          ),
        ),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('默认值缺失时展示默认开启与 2/4096/50/200/90/15/60', (tester) async {
    final service = _FakeConfigService();
    await _pumpCard(tester, service);

    expect(find.text('离线下载'), findsOneWidget);
    expect(_switchTile(tester, '离线下载总开关').value, isTrue);
    expect(_switchTile(tester, '深度分析（无头浏览器）').value, isTrue);

    final fields = tester.widgetList<TextField>(find.byType(TextField)).toList();
    expect(fields.map((f) => f.controller!.text).toList(), [
      '2',
      '4096',
      '50',
      '200',
      '90',
      '15',
      '60',
    ]);
  });

  testWidgets('显式关闭的库展示为关闭', (tester) async {
    final service = _FakeConfigService(
      config: const {'offline_download_enabled': '0'},
    );
    await _pumpCard(tester, service);

    expect(_switchTile(tester, '离线下载总开关').value, isFalse);
  });

  testWidgets('关闭总开关写入 offline_download_enabled=0', (tester) async {
    final service = _FakeConfigService();
    await _pumpCard(tester, service);

    await tester.tap(find.text('离线下载总开关'));
    await tester.pumpAndSettle();

    expect(
      service.updates.any(
        (e) => e.key == 'offline_download_enabled' && e.value == '0',
      ),
      isTrue,
    );
    expect(_switchTile(tester, '离线下载总开关').value, isFalse);
    // 让 SnackBar 计时器走完，避免测试结束时残留 timer
    await tester.pump(const Duration(seconds: 5));
  });

  testWidgets('保存数值配置逐项写入 7 个键', (tester) async {
    final service = _FakeConfigService();
    await _pumpCard(tester, service);

    await tester.enterText(find.byType(TextField).first, '3');
    // v1.8.2：数字项从 4 增至 7，按钮可能滚出视口，先确保可见再点
    await tester.ensureVisible(find.text('保存数值配置'));
    await tester.tap(find.text('保存数值配置'));
    await tester.pumpAndSettle();

    expect(
      service.updates.map((e) => '${e.key}=${e.value}').toList(),
      [
        'offline_download_max_concurrent=3',
        'offline_download_max_file_size_mb=4096',
        'offline_download_max_items=50',
        'offline_download_max_images=200',
        'offline_download_analyze_timeout_s=90',
        'offline_download_connect_timeout_s=15',
        'offline_download_read_timeout_s=60',
      ],
    );
    await tester.pump(const Duration(seconds: 5));
  });

  // ── v1.9.1：存储与临时文件卡片精简为 3 项 ──────────────────

  testWidgets('存储卡片只保留：开关 + 期限 + 间隔 + 立即清理', (tester) async {
    final service = _FakeConfigService(
      config: const {
        'upload_resume_ttl_hours': '36',
        'upload_cleanup_interval_minutes': '120',
      },
    );
    await _pumpStorageCard(tester, service);

    expect(find.byType(TextField), findsNWidgets(2));
    expect(find.text('临时文件期限（小时）'), findsOneWidget);
    expect(find.text('清理检查间隔（分钟）'), findsOneWidget);
    expect(find.text('保存配置'), findsOneWidget);
    expect(find.text('立即清理'), findsOneWidget);
    // 已删除的参数不得回归
    expect(find.text('允许自动清理的客户端类型'), findsNothing);
    expect(find.textContaining('弱服务端'), findsNothing);
    expect(find.textContaining('孤儿'), findsNothing);
    expect(find.textContaining('并发'), findsNothing);
    expect(find.textContaining('会话上限'), findsNothing);
  });

  testWidgets('保存配置只写 3 个键', (tester) async {
    final service = _FakeConfigService(
      config: const {
        'upload_resume_ttl_hours': '36',
        'upload_cleanup_interval_minutes': '120',
      },
    );
    await _pumpStorageCard(tester, service);

    await tester.tap(find.text('保存配置'));
    await tester.pumpAndSettle();

    expect(
      service.updates.map((e) => '${e.key}=${e.value}').toList(),
      [
        'upload_cleanup_enabled=1',
        'upload_resume_ttl_hours=36',
        'upload_cleanup_interval_minutes=120',
      ],
    );
    await tester.pump(const Duration(seconds: 5));
  });

  test('系统设置源码锁死：文案无版本号、无已删除参数', () {
    final source =
        File('lib/pages/admin/admin_system_page.dart').readAsStringSync();
    // 用户可见文案不得出现「v1.4.2 起」这类版本号（口径对齐桌面端/Web 端）
    expect(
      RegExp(r'v\d+\.\d+(\.\d+)?\s*起').hasMatch(source),
      isFalse,
      reason: 'UI 文案不得写版本号字面量',
    );
    expect(source.contains('弱服务端'), isFalse);
    expect(source.contains('upload_cleanup_client_types'), isFalse);
    expect(source.contains('upload_orphan_ttl_hours'), isFalse);
  });
}
