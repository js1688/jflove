import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// 异步失败态呈现测试（v1.5.0）
///
/// 背景（实测踩到的坑）：Riverpod 3 默认给**所有**失败 Provider 加自动重试
/// —— `ProviderContainer.defaultRetry` 最多重试 10 次、指数退避到 6.4 秒。
/// 在这约 30 秒里 `AsyncValue` 是「hasError 但仍 isRefreshing」的状态，
/// `ref.watch(p).when(...)` 会一直走 **loading 分支**：用户看到的是骨架屏，
/// 而不是「加载失败 + 重试」。网络不通时表现为页面永远在转圈。
///
/// 因此 `main.dart` 显式建了 `ProviderContainer(retry: (_, _) => null)`。
/// 本测试把这条决策锁死：任何页面用 `AsyncValue.when` 时，失败必须立刻
/// 落到 error 分支。
void main() {
  /// 与 `main.dart` 保持一致的容器配置（改为默认值就会让本测试失败）
  ProviderContainer buildContainer() => ProviderContainer(retry: (_, _) => null);

  final failing = FutureProvider<int>((ref) async {
    throw StateError('boom');
  });

  test('失败 provider 立刻进入 error 状态，不经过重试等待期', () async {
    final container = buildContainer();
    addTearDown(container.dispose);

    // 触发一次读取并等它结束
    await expectLater(container.read(failing.future), throwsA(isA<StateError>()));

    final value = container.read(failing);
    expect(value.hasError, isTrue, reason: '失败必须立刻可观测');
    expect(
      value.isLoading,
      isFalse,
      reason: '失败后不应停留在 loading（那会让 UI 一直显示骨架屏）',
    );
  });

  testWidgets('AsyncValue.when 在失败后走 error 分支（不是 loading）', (tester) async {
    final container = buildContainer();
    addTearDown(container.dispose);

    final seen = <String>[];
    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp(
          home: Consumer(
            builder: (context, ref, _) {
              return ref.watch(failing).when(
                data: (v) {
                  seen.add('data');
                  return const Text('data');
                },
                loading: () {
                  seen.add('loading');
                  return const Text('loading');
                },
                error: (e, _) {
                  seen.add('error');
                  return const Text('error');
                },
              );
            },
          ),
        ),
      ),
    );

    // 首帧必然是 loading（future 还没结束）
    await tester.pump();
    // 让 future 完成并冲掉失败
    await tester.pumpAndSettle();

    expect(find.text('error'), findsOneWidget);
    expect(find.text('loading'), findsNothing, reason: '失败后不应再显示 loading');
    expect(seen, contains('error'));

    // 再 pump 一段时间，确认没有被「后台重试」把状态打回 loading
    await tester.pump(const Duration(seconds: 1));
    expect(find.text('error'), findsOneWidget);
    expect(find.text('loading'), findsNothing);
  });

  test('显式 invalidate 后重试（ErrorState 的 onRetry 语义）', () async {
    var attempts = 0;
    final flaky = FutureProvider<String>((ref) async {
      attempts++;
      if (attempts == 1) throw StateError('first attempt fails');
      return 'ok';
    });

    final container = buildContainer();
    addTearDown(container.dispose);

    await expectLater(container.read(flaky.future), throwsA(isA<StateError>()));
    expect(attempts, 1, reason: '不应有自动重试，第一次失败后就停');

    // 模拟用户点「重试」
    container.invalidate(flaky);
    expect(await container.read(flaky.future), 'ok');
    expect(attempts, 2);
  });
}
