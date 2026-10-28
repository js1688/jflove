import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

/// v1.8.4 / F45：大文件「校验中」必须显示进度，而不是静止文案
///
/// 说明：传输任务卡是私有 Widget（`_TransferTaskCard`），直接渲染需要会话、
/// 相册、修复中心等一整套 Provider；这里用源码断言锁住三条**可判定**的界面契约，
/// 行为正确性由 `transfer_service_test.dart`（hashPercent 上报）覆盖。
void main() {
  late String transferPage;
  late String floatingCard;

  setUpAll(() {
    transferPage = File(
      'lib/pages/transfer/transfer_page.dart',
    ).readAsStringSync();
    floatingCard = File(
      'lib/widgets/transfer_floating_card.dart',
    ).readAsStringSync();
  });

  test('校验中徽标带百分比', () {
    expect(
      transferPage.contains("'校验中 \${task.hashPercent}%'"),
      isTrue,
      reason: '大文件不能只显示静止的「校验中」',
    );
  });

  test('校验阶段进度条取校验进度，尺寸行显示「已校验」', () {
    expect(
      RegExp(r'task\.status\s*==\s*TaskStatus\.hashing').hasMatch(transferPage),
      isTrue,
    );
    expect(transferPage.contains('task.hashPercent / 100.0'), isTrue);
    expect(transferPage.contains('已校验 '), isTrue);
  });

  test('浮动卡片平均进度不被校验中的 0% 拖住', () {
    expect(
      floatingCard.contains(
        't.status == TaskStatus.hashing ? t.hashPercent : t.percent',
      ),
      isTrue,
    );
  });
}
