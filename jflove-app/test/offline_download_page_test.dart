import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jflove_app/config/theme.dart';
import 'package:jflove_app/models/offline_task.dart';
import 'package:jflove_app/pages/offline/offline_download_page.dart';
import 'package:jflove_app/providers/offline_provider.dart';
import 'package:jflove_app/services/offline_download_service.dart';
import 'package:jflove_app/utils/http_service.dart';
import 'package:jflove_app/utils/session.dart';

/// 假只读服务：记录调用次数，永不出网
class _FakeOfflineService extends OfflineDownloadService {
  _FakeOfflineService({
    required this.list,
    this.detailResult,
    this.metaResult = OfflineMeta.empty,
  }) : super(HttpService(SessionManager()));

  final OfflineTaskList list;
  final OfflineTaskDetail? detailResult;
  final OfflineMeta metaResult;

  int tasksCalls = 0;
  int detailCalls = 0;
  int metaCalls = 0;

  @override
  Future<OfflineTaskList> tasks({int page = 1, int pageSize = 20}) async {
    tasksCalls++;
    return list;
  }

  @override
  Future<OfflineTaskDetail> detail(int taskId) async {
    detailCalls++;
    return detailResult ?? OfflineTaskDetail(task: list.tasks.first);
  }

  @override
  Future<OfflineMeta> meta() async {
    metaCalls++;
    return metaResult;
  }
}

const _doneTask = OfflineTask(
  id: 1,
  username: 'alice',
  sourceDomain: 'example.com',
  pageTitle: '演示产物',
  status: 'success',
  statusText: '已完成',
  itemTotal: 2,
  itemDone: 2,
  bytesTotal: 2048,
  bytesDone: 2048,
  targetDiskName: '主磁盘',
  targetRelPath: 'downloads',
  createdAt: '2026-10-20T10:00:00+00:00',
);

const _failedTask = OfflineTask(
  id: 2,
  username: 'alice',
  sourceDomain: 'video.example.org',
  pageTitle: '失败产物',
  status: 'failed',
  statusText: '失败',
  itemTotal: 1,
  errorCode: 'disk_full',
  errorMessage: '磁盘剩余空间不足',
  targetDiskName: '主磁盘',
  targetRelPath: 'downloads',
  createdAt: '2026-10-20T09:00:00+00:00',
);

/// 只读页测试（v1.8.0 / AC-231 / AC-232）
///
/// 三条事实必须锁死：
///   ① 列表能显示状态 / 进度 / 来源域名 / 产物名 / 目标 / 失败原因；
///   ② **没有任何操作入口**（无按钮、无勾选控件，也不出现被禁的操作文案）；
///   ③ 2.5s 轮询只在页面可见时进行，切后台立刻停（AC-232）。
void main() {
  /// 挂载页面（首屏会立刻拉一次任务 + 元信息）
  Future<_FakeOfflineService> pumpPage(
    WidgetTester tester, {
    List<OfflineTask> tasks = const [_doneTask, _failedTask],
    OfflineTaskDetail? detailResult,
    OfflineMeta meta = OfflineMeta.empty,
  }) async {
    final fake = _FakeOfflineService(
      list: OfflineTaskList(total: tasks.length, tasks: tasks),
      detailResult: detailResult,
      metaResult: meta,
    );
    await tester.pumpWidget(
      ProviderScope(
        overrides: [offlineDownloadServiceProvider.overrideWithValue(fake)],
        child: MaterialApp(
          theme: AppTheme.light,
          home: const OfflineDownloadPage(),
        ),
      ),
    );
    // 首帧 + 首屏数据到达后的重建
    await tester.pump();
    await tester.pump();
    return fake;
  }

  /// 卸载页面 → dispose 取消轮询（不给测试留 pending timer）
  Future<void> unmount(WidgetTester tester) async {
    await tester.pumpWidget(const SizedBox());
    await tester.pump();
  }

  testWidgets('列表展示状态/进度/来源域名/产物名/目标/失败原因', (tester) async {
    final fake = await pumpPage(tester);

    expect(fake.tasksCalls, 1, reason: '首屏拉一次任务列表');
    expect(fake.metaCalls, 1, reason: '首屏拉一次元信息');

    // 产物名
    expect(find.text('演示产物'), findsOneWidget);
    expect(find.text('失败产物'), findsOneWidget);
    // 状态（服务端下发的中文文案，客户端不自行翻译）
    expect(find.text('已完成'), findsOneWidget);
    expect(find.text('失败'), findsOneWidget);
    // 来源域名
    expect(find.textContaining('example.com'), findsOneWidget);
    expect(find.textContaining('video.example.org'), findsOneWidget);
    // 目标
    expect(find.textContaining('主磁盘/downloads'), findsNWidgets(2));
    // 进度百分比 + 字节
    expect(find.text('100%'), findsOneWidget);
    expect(find.textContaining('2.0 KB / 2.0 KB'), findsOneWidget);
    // 失败原因
    expect(find.text('磁盘剩余空间不足'), findsOneWidget);

    await unmount(tester);
  });

  testWidgets('只读：无任何操作按钮/勾选控件，也不出现被禁文案', (tester) async {
    await pumpPage(tester);

    // 唯一允许的按钮是 AppBar 的刷新（不是任务操作）
    expect(find.byType(FilledButton), findsNothing);
    expect(find.byType(ElevatedButton), findsNothing);
    expect(find.byType(TextButton), findsNothing);
    expect(find.byType(Checkbox), findsNothing);
    expect(find.byType(Switch), findsNothing);
    expect(find.byType(TextField), findsNothing);

    // AC-231：0 命中「新建任务 / 开始下载 / 分析」；「操作类」文案同样不得出现
    for (final banned in const ['新建任务', '开始下载', '分析', '取消', '重试', '勾选']) {
      expect(
        find.textContaining(banned),
        findsNothing,
        reason: '移动端只读页不得出现「$banned」',
      );
    }

    await unmount(tester);
  });

  testWidgets('轮询纪律：前台 2.5s 一轮，切后台立即停止，回前台恢复（AC-232）', (tester) async {
    final fake = await pumpPage(tester);
    final initialCalls = fake.tasksCalls;
    expect(initialCalls, 1);

    // 前台：2.5s 后应有一轮新请求
    await tester.pump(const Duration(milliseconds: 2600));
    expect(fake.tasksCalls, greaterThan(initialCalls));

    // 切后台：轮询立即停（不必等 5 秒缓冲）
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    await tester.pump();
    final pausedCalls = fake.tasksCalls;
    await tester.pump(const Duration(seconds: 10));
    expect(fake.tasksCalls, pausedCalls, reason: '页面不可见时不得继续轮询（AC-232）');

    // 回前台：恢复轮询 + 立刻补一次
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pump(const Duration(milliseconds: 2600));
    expect(fake.tasksCalls, greaterThan(pausedCalls));

    await unmount(tester);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
  });

  testWidgets('详情只读：来源域名/产物名/目标目录/失败原因 + 清单项', (tester) async {
    final fake = await pumpPage(
      tester,
      detailResult: const OfflineTaskDetail(
        task: _failedTask,
        items: [
          OfflineItem(
            id: 11,
            kind: 'video',
            displayName: '片段 1',
            ext: 'mp4',
            width: 1920,
            height: 1080,
            durationMs: 65000,
            estSize: 4096,
            isEstimated: true,
          ),
        ],
        hasMoreImages: true,
      ),
    );

    await tester.tap(find.text('失败产物'));
    await tester.pumpAndSettle();

    expect(fake.detailCalls, 1);
    expect(find.text('任务详情'), findsOneWidget);
    // 需求要求详情展示的四项
    expect(find.text('来源域名'), findsOneWidget);
    expect(find.text('产物名'), findsOneWidget);
    expect(find.text('目标目录'), findsOneWidget);
    expect(find.text('失败原因'), findsOneWidget);
    expect(find.text('video.example.org'), findsOneWidget);
    // 列表卡片（详情背后）与详情行各有一处失败原因
    expect(find.textContaining('磁盘剩余空间不足'), findsNWidgets(2));
    // 清单项（只读展示：名称 + 分辨率 + 时长 + 估算大小）
    expect(find.text('片段 1'), findsOneWidget);
    expect(find.textContaining('1920x1080'), findsOneWidget);
    expect(find.textContaining('1:05'), findsOneWidget);
    expect(find.text('该任务还有更多图片未载入'), findsOneWidget);
    // 详情里同样没有任何输入/操作控件
    expect(find.byType(TextField), findsNothing);
    expect(find.byType(Checkbox), findsNothing);

    // 关闭后回到列表，轮询仍在可见状态
    await tester.tap(find.byIcon(Icons.close_rounded));
    await tester.pumpAndSettle();
    expect(find.text('离线下载中心'), findsOneWidget);

    await unmount(tester);
  });

  testWidgets('元信息不可用时只显示提示条，不提供任何操作', (tester) async {
    await pumpPage(
      tester,
      meta: const OfflineMeta(
        enabled: true,
        canUse: false,
        unusableReason: 'no_writable_disk',
      ),
    );

    expect(find.text('你没有可写入的磁盘，暂时无法使用离线下载'), findsOneWidget);
    expect(find.byType(TextButton), findsNothing);
    expect(find.byType(FilledButton), findsNothing);

    await unmount(tester);
  });

  testWidgets('加载失败显示错误态（重新加载），不出现任务操作文案', (tester) async {
    final fake = _FailingOfflineService();
    await tester.pumpWidget(
      ProviderScope(
        overrides: [offlineDownloadServiceProvider.overrideWithValue(fake)],
        child: MaterialApp(
          theme: AppTheme.light,
          home: const OfflineDownloadPage(),
        ),
      ),
    );
    await tester.pump();
    await tester.pump();

    expect(find.text('任务加载失败'), findsOneWidget);
    expect(find.text('重新加载'), findsOneWidget);
    for (final banned in const ['新建任务', '开始下载', '分析', '取消', '重试']) {
      expect(find.textContaining(banned), findsNothing);
    }

    await unmount(tester);
  });
}

/// 只读服务：任务列表恒失败（验证错误态）
class _FailingOfflineService extends OfflineDownloadService {
  _FailingOfflineService() : super(HttpService(SessionManager()));

  @override
  Future<OfflineTaskList> tasks({int page = 1, int pageSize = 20}) async {
    throw StateError('boom');
  }

  @override
  Future<OfflineMeta> meta() async => OfflineMeta.empty;
}
