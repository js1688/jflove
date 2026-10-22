import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:jflove_app/config/theme.dart';
import 'package:jflove_app/models/offline_task.dart';
import 'package:jflove_app/pages/offline/offline_download_page.dart';
import 'package:jflove_app/providers/offline_provider.dart';
import 'package:jflove_app/services/offline_download_service.dart';
import 'package:jflove_app/utils/http_service.dart';
import 'package:jflove_app/utils/session.dart';

/// 假离线下载服务：记录调用次数，永不出网
class _FakeOfflineService extends OfflineDownloadService {
  _FakeOfflineService({
    required List<OfflineTask> tasks,
    this.detailResult,
    this.metaResult = _readyMeta,
  }) : _tasks = List<OfflineTask>.of(tasks),
       super(HttpService(SessionManager()));

  final List<OfflineTask> _tasks;
  final OfflineTaskDetail? detailResult;
  final OfflineMeta metaResult;

  int tasksCalls = 0;
  int detailCalls = 0;
  int metaCalls = 0;
  int cancelCalls = 0;
  int retryCalls = 0;
  int deleteCalls = 0;

  @override
  Future<OfflineTaskList> tasks({int page = 1, int pageSize = 20}) async {
    tasksCalls++;
    return OfflineTaskList(total: _tasks.length, tasks: _tasks);
  }

  @override
  Future<OfflineTaskDetail> detail(int taskId) async {
    detailCalls++;
    if (detailResult != null) return detailResult!;
    return OfflineTaskDetail(task: _tasks.first);
  }

  @override
  Future<OfflineMeta> meta() async {
    metaCalls++;
    return metaResult;
  }

  @override
  Future<OfflineTaskAction> cancel(int taskId) async {
    cancelCalls++;
    return OfflineTaskAction(taskId: taskId, status: 'canceled');
  }

  @override
  Future<OfflineTaskAction> retry(int taskId) async {
    retryCalls++;
    return OfflineTaskAction(taskId: taskId, status: 'downloading');
  }

  @override
  Future<OfflineTaskAction> deleteRecord(int taskId) async {
    deleteCalls++;
    _tasks.removeWhere((task) => task.id == taskId);
    return OfflineTaskAction(taskId: taskId, deleted: true);
  }
}

/// 默认元信息：已配置目录、有可写磁盘（任务中心可用）
const _readyMeta = OfflineMeta(
  enabled: true,
  canUse: true,
  configured: true,
  configDiskId: 1,
  configRelPath: 'downloads',
  configValid: true,
  writableDisks: [OfflineWritableDisk(diskId: 1, name: '主磁盘')],
);

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

const _canceledTask = OfflineTask(
  id: 4,
  username: 'alice',
  sourceDomain: 'example.org',
  pageTitle: '已取消产物',
  status: 'canceled',
  statusText: '已取消',
  itemTotal: 1,
  bytesTotal: 1024,
  bytesDone: 256,
  targetDiskName: '主磁盘',
  targetRelPath: 'downloads',
  createdAt: '2026-10-20T07:00:00+00:00',
);

const _activeTask = OfflineTask(
  id: 3,
  username: 'alice',
  sourceDomain: 'example.net',
  pageTitle: '进行中产物',
  status: 'downloading',
  statusText: '下载中',
  itemTotal: 4,
  itemDone: 1,
  bytesTotal: 4096,
  bytesDone: 1024,
  targetDiskName: '主磁盘',
  targetRelPath: 'downloads',
  createdAt: '2026-10-20T08:00:00+00:00',
  canOperate: true,
);

/// 任务中心测试（v1.8.1 / AC-248~250、AC-232）
///
/// 三条事实必须锁死：
///   ① 列表能显示状态 / 进度 / 来源域名 / 产物名 / 目标 / 失败原因；
///   ② 有完整操作入口（新建 / 设置 / 取消 / 重试 / 删除记录）；
///   ③ 2.5s 轮询只在页面可见时进行，切后台立刻停（AC-232）。
void main() {
  Future<_FakeOfflineService> pumpPage(
    WidgetTester tester, {
    List<OfflineTask> tasks = const [_doneTask, _failedTask],
    OfflineTaskDetail? detailResult,
    OfflineMeta meta = _readyMeta,
  }) async {
    final fake = _FakeOfflineService(
      tasks: tasks,
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

    expect(find.text('演示产物'), findsOneWidget);
    expect(find.text('失败产物'), findsOneWidget);
    expect(find.text('已完成'), findsOneWidget);
    expect(find.text('失败'), findsOneWidget);
    expect(find.textContaining('example.com'), findsOneWidget);
    expect(find.textContaining('video.example.org'), findsOneWidget);
    expect(find.textContaining('主磁盘/downloads'), findsNWidgets(2));
    expect(find.text('100%'), findsOneWidget);
    expect(find.textContaining('2.0 KB / 2.0 KB'), findsOneWidget);
    expect(find.textContaining('磁盘剩余空间不足'), findsOneWidget);

    await unmount(tester);
  });

  testWidgets('完整功能入口：新建任务 / 设置 / 取消 / 重试 / 删除记录', (tester) async {
    final fake = await pumpPage(
      tester,
      tasks: const [_activeTask, _doneTask, _canceledTask],
    );

    // 新建入口（FAB + 空态动作）与设置入口
    expect(find.text('新建任务'), findsWidgets);
    expect(find.byTooltip('设置'), findsOneWidget);
    // 进行中 → 取消；失败 → 重试；终态 → 删除记录
    expect(find.text('取消'), findsOneWidget);
    expect(find.text('重试'), findsOneWidget);
    expect(find.text('删除记录'), findsAtLeastNWidgets(2));

    // 删除记录：调服务 + 刷新列表
    await tester.tap(find.text('删除记录').first);
    await tester.pump();
    await tester.pump();
    expect(fake.deleteCalls, 1);

    await unmount(tester);
  });

  testWidgets('轮询纪律：前台 2.5s 一轮，切后台立即停止，回前台恢复（AC-232）', (tester) async {
    final fake = await pumpPage(tester);
    final initialCalls = fake.tasksCalls;
    expect(initialCalls, 1);

    await tester.pump(const Duration(milliseconds: 2600));
    expect(fake.tasksCalls, greaterThan(initialCalls));

    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    await tester.pump();
    final pausedCalls = fake.tasksCalls;
    await tester.pump(const Duration(seconds: 10));
    expect(fake.tasksCalls, pausedCalls, reason: '页面不可见时不得继续轮询（AC-232）');

    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pump(const Duration(milliseconds: 2600));
    expect(fake.tasksCalls, greaterThan(pausedCalls));

    await unmount(tester);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
  });

  testWidgets('详情：来源域名/产物名/目标目录/失败原因 + 清单项 + 操作', (tester) async {
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
    expect(find.text('来源域名'), findsOneWidget);
    expect(find.text('产物名'), findsOneWidget);
    expect(find.text('目标目录'), findsOneWidget);
    expect(find.text('失败原因'), findsOneWidget);
    expect(find.text('video.example.org'), findsOneWidget);
    // 失败原因在卡片 + 详情各一处，并带中文建议
    expect(find.textContaining('磁盘剩余空间不足'), findsNWidgets(2));
    expect(find.textContaining('清理目标磁盘空间后重试'), findsWidgets);
    // 清单项（名称 + 分辨率 + 时长 + 估算大小）
    expect(find.text('片段 1'), findsOneWidget);
    expect(find.textContaining('1920x1080'), findsOneWidget);
    expect(find.textContaining('1:05'), findsOneWidget);
    // disk_full 属于不可重试类型：详情只提供删除记录
    expect(find.text('删除记录'), findsWidgets);

    await tester.tap(find.byIcon(Icons.close_rounded));
    await tester.pumpAndSettle();
    expect(find.text('离线下载中心'), findsOneWidget);

    await unmount(tester);
  });

  testWidgets('元信息不可用：显示中文原因，不阻断查看已有任务', (tester) async {
    await pumpPage(
      tester,
      meta: const OfflineMeta(
        enabled: true,
        canUse: false,
        unusableReason: 'no_writable_disk',
      ),
    );

    expect(find.text('你没有可写入的磁盘，暂时无法使用离线下载'), findsOneWidget);
    // 仍可查看已有任务与进入新建页（新建页会再拦截）
    expect(find.text('演示产物'), findsOneWidget);
    expect(find.text('新建任务'), findsWidgets);

    await unmount(tester);
  });


  testWidgets('「新建任务」通过 go_router 打开 /offline/new（回归：不能用 pushNamed）', (tester) async {
    final fake = _FakeOfflineService(tasks: const []);
    final router = GoRouter(
      initialLocation: '/offline',
      routes: [
        GoRoute(
          path: '/offline',
          builder: (_, _) => const OfflineDownloadPage(),
        ),
        GoRoute(
          path: '/offline/new',
          builder: (_, _) => const Scaffold(body: Text('新建任务页')),
        ),
      ],
    );
    await tester.pumpWidget(
      ProviderScope(
        overrides: [offlineDownloadServiceProvider.overrideWithValue(fake)],
        child: MaterialApp.router(
          theme: AppTheme.light,
          routerConfig: router,
        ),
      ),
    );
    await tester.pump();
    await tester.pump();

    await tester.tap(find.text('新建任务').first);
    await tester.pumpAndSettle();

    expect(find.text('新建任务页'), findsOneWidget);

    await unmount(tester);
  });

  testWidgets('加载失败显示错误态（重新加载）', (tester) async {
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
