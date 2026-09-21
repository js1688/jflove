import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

/// 底部导航回归测试（v1.5.0）
///
/// v1.5.0 把底部导航由 6 项收敛为 5 项（「修复中心」并入「传输」页第二个
/// 页签）。这类结构性改动有两个容易踩的坑，本测试专门锁死：
///
///   1. **路由必须保留**：`/repair` 是旧版本会写入历史 / 可能被外部跳转的
///      路径，删掉会让老用户点进去白屏；
///   2. **高亮必须跟着走**：`/repair` 不再对应导航项，若按下标硬算会回落
///      到「文件」，表现为"进了修复中心但底部高亮在文件上"。
void main() {
  late String appSource;

  setUpAll(() {
    appSource = File('lib/app.dart').readAsStringSync();
  });

  test('底部导航恰好 5 项，且顺序为 文件/笔记/同步/传输任务/设置', () {
    final labels = RegExp(r"label: '([^']+)'")
        .allMatches(appSource)
        .map((m) => m.group(1)!)
        .toList();

    // app.dart 里只有底部导航会写 label:，若将来新增需同步调整本断言
    expect(
      labels,
      ['文件', '笔记', '同步', '传输任务', '设置'],
      reason: '底部导航项与顺序必须与设计文档一致（6 → 5）',
    );
    expect(labels.length, 5);
    expect(labels, isNot(contains('修复中心')), reason: '修复中心已并入传输页页签');
  });

  test('导航路由表与导航项数量一致，且不含 /repair', () {
    final routes = RegExp(r"'(/[a-z]+)'")
        .allMatches(appSource)
        .map((m) => m.group(1)!)
        .toSet();

    // _routes 常量里必须没有 /repair（它不是导航项）
    final routesBlock = RegExp(
      r'static const List<String> _routes = \[(.*?)\];',
      dotAll: true,
    ).firstMatch(appSource);
    expect(routesBlock, isNotNull, reason: '未找到 _routes 常量，测试需要更新');

    final navRoutes = RegExp(r"'([^']+)'")
        .allMatches(routesBlock!.group(1)!)
        .map((m) => m.group(1)!)
        .toList();
    expect(navRoutes, ['/files', '/notes', '/sync', '/transfer', '/settings']);
    expect(navRoutes, isNot(contains('/repair')));

    // 路由本身（GoRoute）仍然要保留 /repair
    expect(routes, contains('/repair'), reason: '/repair 旧路径必须保留，避免旧跳转白屏');
  });

  test('/repair 仍注册为路由，并落到传输页第二个页签', () {
    expect(
      RegExp(r"path: '/repair'").hasMatch(appSource),
      isTrue,
      reason: '/repair 路由被删除了，旧书签 / 通知跳转会 404',
    );
    // /repair 指向 TransferPage(initialTab: 1)，即修复中心页签
    final repairBlock = RegExp(
      r"path: '/repair',\s*builder: \(_, _\) => ([^,]+),",
      dotAll: true,
    ).firstMatch(appSource);
    expect(repairBlock, isNotNull, reason: '无法解析 /repair 的 builder');
    expect(repairBlock!.group(1), contains('TransferPage'));
    expect(repairBlock.group(1), contains('initialTab: 1'));
  });

  test('/repair 在底部导航里必须归一化到 /transfer（高亮不回落）', () {
    expect(
      appSource.contains("location == '/repair' ? '/transfer' : location"),
      isTrue,
      reason: '缺少 /repair → /transfer 的高亮归一化，底部会错误高亮「文件」',
    );
  });

  test('主题模式接入 MaterialApp.router（不再写死 ThemeMode.system）', () {
    expect(appSource.contains('themeMode: toMaterialThemeMode(themeMode)'), isTrue);
    expect(appSource.contains('ref.watch(themeModeProvider)'), isTrue);
    expect(
      appSource.contains('themeMode: ThemeMode.system'),
      isFalse,
      reason: '主题模式必须来自 themeModeProvider，不能写死',
    );
  });

  test('公开路由的页签索引与 /repair 的 initialTab 都在合法范围', () {
    // TransferPage 只有 2 个页签（传输任务 / 修复中心）
    final m = RegExp(r'initialTab: (\d+)').firstMatch(appSource);
    expect(m, isNotNull);
    expect(int.parse(m!.group(1)!), inInclusiveRange(0, 1));
  });
}
