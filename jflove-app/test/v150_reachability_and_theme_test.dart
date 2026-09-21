import 'dart:io';

import 'package:flutter/material.dart' show ThemeMode;
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:jflove_app/providers/theme_provider.dart';

/// v1.5.0 移动端回归用例：修复中心可达性（AC-18）与主题三态持久化（AC-2）
///
/// 说明：
///   - 结构类断言用源码扫描（导航/页签/操作入口都是声明式常量，渲染断言不增加可信度）；
///   - 主题持久化用 `flutter_secure_storage` 的 **真实 MethodChannel** 打桩，
///     验证的是 `ThemeModeNotifier` 的读写往返，而不是复制一份实现。
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('AC-18 修复中心功能未丢失（并入传输页页签二）', () {
    late String transferPage;
    late String repairPage;
    late String appSource;

    setUpAll(() {
      transferPage = File('lib/pages/transfer/transfer_page.dart').readAsStringSync();
      repairPage = File('lib/pages/repair/repair_center_page.dart').readAsStringSync();
      appSource = File('lib/app.dart').readAsStringSync();
    });

    test('传输页恰好两个页签：传输任务 / 修复中心', () {
      expect(RegExp(r'TabController\(\s*length:\s*2').hasMatch(transferPage), isTrue);
      expect(transferPage.contains("Tab(text: '传输任务')"), isTrue);
      expect(transferPage.contains("Tab(text: '修复中心')"), isTrue);
    });

    test('修复中心内容复用同一个组件，而不是复制一份页面', () {
      expect(
        transferPage.contains("import '../repair/repair_center_page.dart'"),
        isTrue,
      );
      expect(transferPage.contains('RepairCenterContent()'), isTrue);
      expect(repairPage.contains('class RepairCenterContent extends ConsumerStatefulWidget'),
          isTrue);
    });

    test('/repair 旧路径仍然可用并直接落到修复页签', () {
      expect(appSource.contains("path: '/repair'"), isTrue);
      expect(appSource.contains('TransferPage(initialTab: 1)'), isTrue);
      expect(transferPage.contains('initialIndex: widget.initialTab.clamp(0, 1)'), isTrue);
    });

    test('修复中心的全部操作入口仍在（查看/取消/验证播放/覆盖/删除产物/删除记录）', () {
      for (final action in ['取消', '验证播放', '覆盖原文件', '删除产物', '删除记录']) {
        expect(repairPage.contains(action), isTrue, reason: '缺少「$action」入口');
      }
      // 覆盖是破坏性操作，必须有二次确认
      expect(repairPage.contains('覆盖原文件（不可恢复）'), isTrue);
      expect(repairPage.contains('确认覆盖'), isTrue);
    });

    test('任务列表具备加载态 / 空态 / 错误态（AC-6）', () {
      expect(repairPage.contains('ErrorState('), isTrue);
      expect(repairPage.contains('EmptyState('), isTrue);
      expect(repairPage.contains('修复任务加载失败'), isTrue);
      expect(repairPage.contains('暂无修复任务'), isTrue);
    });
  });

  group('AC-2 主题三态持久化（真实 secure storage 通道打桩）', () {
    const channel = MethodChannel('plugins.it_nomads.com/flutter_secure_storage');
    final store = <String, String>{};

    setUp(() {
      store.clear();
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(channel, (call) async {
        final args = (call.arguments as Map?)?.cast<String, Object?>() ?? const {};
        switch (call.method) {
          case 'read':
            return store[args['key'] as String?];
          case 'write':
            store[args['key'] as String] = args['value'] as String;
            return null;
          case 'delete':
            store.remove(args['key'] as String?);
            return null;
          case 'containsKey':
            return store.containsKey(args['key'] as String?);
          case 'readAll':
            return Map<String, String>.from(store);
          case 'deleteAll':
            store.clear();
            return null;
          default:
            return null;
        }
      });
    });

    tearDown(() {
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(channel, null);
    });

    ProviderContainer buildContainer() => ProviderContainer(retry: (_, _) => null);

    test('存储键名稳定（改键名会让老用户主题偏好失效）', () {
      expect(ThemeModeNotifier.storageKey, 'theme_mode');
    });

    test('默认跟随系统；切换后写入持久化标识', () async {
      final container = buildContainer();
      addTearDown(container.dispose);

      expect(container.read(themeModeProvider), AppThemeMode.system);

      await container.read(themeModeProvider.notifier).setMode(AppThemeMode.dark);
      expect(container.read(themeModeProvider), AppThemeMode.dark);
      expect(store['theme_mode'], 'dark');

      await container.read(themeModeProvider.notifier).setMode(AppThemeMode.light);
      expect(store['theme_mode'], 'light');
    });

    test('load() 从持久化恢复（runApp 前调用，避免"先亮后暗"闪烁）', () async {
      store['theme_mode'] = 'dark';

      final container = buildContainer();
      addTearDown(container.dispose);
      await container.read(themeModeProvider.notifier).load();

      expect(container.read(themeModeProvider), AppThemeMode.dark);
      expect(toMaterialThemeMode(container.read(themeModeProvider)), ThemeMode.dark);
    });

    test('持久化值非法时回退跟随系统（不抛异常、不白屏）', () async {
      store['theme_mode'] = 'rainbow';

      final container = buildContainer();
      addTearDown(container.dispose);
      await container.read(themeModeProvider.notifier).load();

      expect(container.read(themeModeProvider), AppThemeMode.system);
    });

    test('cycle() 在 跟随系统 → 亮色 → 暗色 之间循环', () async {
      final container = buildContainer();
      addTearDown(container.dispose);

      final notifier = container.read(themeModeProvider.notifier);
      expect(container.read(themeModeProvider), AppThemeMode.system);

      await notifier.cycle();
      expect(container.read(themeModeProvider), AppThemeMode.light);
      await notifier.cycle();
      expect(container.read(themeModeProvider), AppThemeMode.dark);
      await notifier.cycle();
      expect(container.read(themeModeProvider), AppThemeMode.system);
      expect(store['theme_mode'], 'system');
    });

    test('存储不可用时切换仍然生效（只记日志，不影响本次会话）', () async {
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(channel, (call) async {
        throw PlatformException(code: 'storage_unavailable', message: 'keystore 异常');
      });

      final container = buildContainer();
      addTearDown(container.dispose);
      final notifier = container.read(themeModeProvider.notifier);

      await notifier.setMode(AppThemeMode.dark);
      expect(container.read(themeModeProvider), AppThemeMode.dark);

      await notifier.load();
      expect(container.read(themeModeProvider), AppThemeMode.system);
    });

    test('三态到 Material ThemeMode 的映射完整（AC-2）', () {
      expect(toMaterialThemeMode(AppThemeMode.system), ThemeMode.system);
      expect(toMaterialThemeMode(AppThemeMode.light), ThemeMode.light);
      expect(toMaterialThemeMode(AppThemeMode.dark), ThemeMode.dark);
    });
  });
}
