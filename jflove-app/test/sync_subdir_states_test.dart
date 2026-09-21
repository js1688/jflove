import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

/// 同步页「远端子目录列表」的状态机回归测试（v1.5.0 反馈修复）
///
/// 修复的缺陷（静默失败类）：`_loadSubdirs` 的 catch 原本只把 `_subdirs` 清空，
/// 于是**网络读取失败会被界面显示成「此目录下没有子文件夹」** ——
/// 用户会据此以为远端目录是空的，而且没有任何重试入口。
///
/// 正确行为是三态：加载中 → 读取失败（可重试）→ 确实是空。
/// 本文件用**源码级断言**锁住这条（页面依赖网络服务与 Riverpod，构造完整
/// widget 测试的成本远高于它要保护的东西；真正跑起来的行为由 APK 冒烟覆盖）。
void main() {
  const pagePath = 'lib/pages/sync/sync_page.dart';
  late String src;

  setUpAll(() {
    src = File(pagePath).readAsStringSync();
  });

  group('远端子目录列表必须区分「读取失败」与「空目录」', () {
    test('存在独立的失败状态字段（不能只靠清空列表表达失败）', () {
      expect(
        src.contains('String? _subdirsError;'),
        isTrue,
        reason: '缺少 _subdirsError：失败时只能清空列表，界面会把失败显示成空目录',
      );
    });

    test('加载开始时先清掉上一次的错误（重试后错误态不能一直盖住新状态）', () {
      final load = src.split('Future<void> _loadSubdirs()').last;
      final clearAt = load.indexOf('_subdirsError = null;');
      final tryAt = load.indexOf('try {');
      expect(clearAt, greaterThan(-1), reason: '_loadSubdirs 里没有清理 _subdirsError');
      expect(tryAt, greaterThan(-1));
      expect(clearAt, lessThan(tryAt), reason: '清理必须发生在发起请求之前');
    });

    test('catch 里记录错误（而不是默默清空列表）', () {
      final load = src.split('Future<void> _loadSubdirs()').last;
      final catchAt = load.indexOf('} catch (e) {');
      expect(catchAt, greaterThan(-1), reason: '_loadSubdirs 缺少 catch');
      final catchBody = load.substring(catchAt, load.indexOf('\n  }', catchAt));
      expect(
        catchBody.contains('_subdirsError = e.toString();'),
        isTrue,
        reason: 'catch 里没有记录错误信息 → 静默失败',
      );
    });

    test('渲染处三态齐全：加载中 / 读取失败(可重试) / 空', () {
      // 失败态必须排在空态之前，否则空态会先命中
      final errAt = src.indexOf("title: '目录读取失败',");
      expect(errAt, greaterThan(-1), reason: '远端列表没有「目录读取失败」错误态');
      final errBlock = src.substring(errAt, errAt + 400);
      expect(errBlock.contains('onRetry: _loadSubdirs'), isTrue,
          reason: '错误态必须提供重试入口');
      final emptyAt = src.indexOf("title: '此目录下没有子文件夹',");
      expect(emptyAt, greaterThan(-1), reason: '缺少空态');
      // 三态判定顺序：_loadingDirs → _subdirsError → _subdirs.isEmpty
      final renderAt = src.indexOf('_loadingDirs\n');
      expect(renderAt, greaterThan(-1));
      expect(src.indexOf('_subdirsError != null'), greaterThan(-1));
      expect(renderAt, lessThan(errAt), reason: '加载中判定必须在错误态之前');
      expect(errAt, lessThan(emptyAt), reason: '错误态必须在空态之前（否则失败被显示成空）');
    });
  });
}
