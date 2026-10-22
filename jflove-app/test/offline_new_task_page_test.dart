import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jflove_app/config/theme.dart';
import 'package:jflove_app/models/offline_task.dart';
import 'package:jflove_app/pages/offline/offline_new_task_page.dart';
import 'package:jflove_app/providers/offline_provider.dart';
import 'package:jflove_app/services/offline_download_service.dart';
import 'package:jflove_app/utils/http_service.dart';
import 'package:jflove_app/utils/session.dart';

/// 假离线下载服务：analyze / detail / select 都记录参数，永不出网
class _FakeOfflineService extends OfflineDownloadService {
  _FakeOfflineService({
    this.metaResult = _readyMeta,
    required this.details,
    this.failDetail = false,
  }) : super(HttpService(SessionManager()));

  final OfflineMeta metaResult;
  final List<OfflineTaskDetail> details;
  final bool failDetail;

  int analyzeCalls = 0;
  int selectCalls = 0;
  int cancelCalls = 0;
  int detailIndex = 0;
  int? lastTaskId;
  List<int>? lastItemIds;
  int? lastDiskId;
  String? lastTargetPath;
  bool? lastPackZip;

  @override
  Future<OfflineMeta> meta() async => metaResult;

  @override
  Future<OfflineAnalyzeResult> analyze(String url) async {
    analyzeCalls++;
    return const OfflineAnalyzeResult(
      taskId: 42,
      status: 'analyzing',
      sourceDomain: 'example.com',
    );
  }

  @override
  Future<OfflineTaskDetail> detail(int taskId) async {
    if (failDetail) throw StateError('poll boom');
    final index = detailIndex < details.length ? detailIndex : details.length - 1;
    detailIndex++;
    return details[index];
  }

  @override
  Future<OfflineTaskAction> cancel(int taskId) async {
    cancelCalls++;
    return OfflineTaskAction(taskId: taskId, status: 'canceled');
  }

  @override
  Future<OfflineSelectResult> select({
    required int taskId,
    required List<int> itemIds,
    required int diskId,
    required String targetPath,
    bool packZip = false,
  }) async {
    selectCalls++;
    lastTaskId = taskId;
    lastItemIds = itemIds;
    lastDiskId = diskId;
    lastTargetPath = targetPath;
    lastPackZip = packZip;
    return const OfflineSelectResult(
      taskId: 42,
      status: 'downloading',
      selected: 2,
      bytesTotal: 2048,
    );
  }
}

const _readyMeta = OfflineMeta(
  enabled: true,
  canUse: true,
  configured: true,
  configDiskId: 1,
  configRelPath: 'downloads',
  configValid: true,
  writableDisks: [OfflineWritableDisk(diskId: 1, name: '主磁盘')],
);

const _readyDetail = OfflineTaskDetail(
  task: OfflineTask(
    id: 42,
    status: 'ready',
    statusText: '待选择',
    sourceDomain: 'example.com',
    pageTitle: '演示页面',
    itemTotal: 2,
  ),
  items: [
    OfflineItem(
      id: 1,
      kind: 'video',
      displayName: '1080P 片段',
      ext: 'mp4',
      width: 1920,
      height: 1080,
      durationMs: 65000,
      estSize: 2048,
      isEstimated: true,
    ),
    OfflineItem(
      id: 2,
      kind: 'audio',
      displayName: '仅音频',
      ext: 'm4a',
      estSize: 512,
    ),
  ],
);

const _analyzingDetail = OfflineTaskDetail(
  task: OfflineTask(id: 42, status: 'analyzing', statusText: '分析中'),
);

/// 新建任务页（v1.8.1 / F32.2、AC-248、AC-253）
void main() {
  Future<_FakeOfflineService> pumpPage(
    WidgetTester tester, {
    OfflineMeta meta = _readyMeta,
  }) async {
    final fake = _FakeOfflineService(
      metaResult: meta,
      details: const [_analyzingDetail, _readyDetail, _readyDetail],
    );
    await tester.pumpWidget(
      ProviderScope(
        overrides: [offlineDownloadServiceProvider.overrideWithValue(fake)],
        child: MaterialApp(
          theme: AppTheme.light,
          initialRoute: '/offline/new',
          routes: {
            '/': (_) => const Scaffold(body: Text('任务中心')),
            '/offline/new': (_) => const OfflineNewTaskPage(),
          },
        ),
      ),
    );
    await tester.pump();
    await tester.pump();
    return fake;
  }

  testWidgets('未配置默认目录：不发起分析，给出去设置入口（AC-253）', (tester) async {
    final fake = await pumpPage(
      tester,
      meta: const OfflineMeta(
        enabled: true,
        canUse: true,
        configured: false,
        unusableReason: 'not_configured',
      ),
    );

    expect(find.text('尚未设置离线下载默认目录'), findsOneWidget);
    expect(find.text('去设置'), findsOneWidget);

    // 「分析」按钮禁用
    final analyzeButton = tester.widget<FilledButton>(
      find.widgetWithText(FilledButton, '分析'),
    );
    expect(analyzeButton.onPressed, isNull);

    // 输入 URL 也不会发起请求
    await tester.enterText(find.byType(TextField), 'https://example.com/v');
    await tester.pump();
    expect(fake.analyzeCalls, 0);
  });

  testWidgets('分析 → 轮询 ready → 勾选 → 开始下载（AC-248）', (tester) async {
    final fake = await pumpPage(tester);

    await tester.enterText(find.byType(TextField), 'https://example.com/v');
    await tester.tap(find.text('分析'));
    await tester.pump();
    await tester.pump();
    expect(fake.analyzeCalls, 1);

    // 第一轮轮询返回 analyzing，第二轮 ready（2.5s × 2）
    await tester.pump(const Duration(milliseconds: 2600));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 2600));
    await tester.pumpAndSettle();

    expect(find.text('清单'), findsOneWidget);
    expect(find.text('1080P 片段'), findsOneWidget);
    expect(find.text('仅音频'), findsOneWidget);
    // 默认全选（2 项）
    expect(find.textContaining('已选 2 项'), findsOneWidget);

    await tester.ensureVisible(find.text('开始下载'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('开始下载'));
    await tester.pump();
    await tester.pumpAndSettle();

    expect(fake.selectCalls, 1);
    expect(fake.lastTaskId, 42);
    expect(fake.lastItemIds, containsAll(<int>[1, 2]));
    expect(fake.lastDiskId, 1);
    expect(fake.lastTargetPath, 'downloads');
    expect(fake.lastPackZip, isFalse);
    // 成功后返回任务中心
    expect(find.text('任务中心'), findsOneWidget);
  });

  testWidgets('快捷选择：只要音频只选中音频项', (tester) async {
    final fake = await pumpPage(tester);
    await tester.enterText(find.byType(TextField), 'https://example.com/v');
    await tester.tap(find.text('分析'));
    await tester.pump();
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 2600));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 2600));
    await tester.pumpAndSettle();

    await tester.tap(find.text('只要音频'));
    await tester.pumpAndSettle();
    expect(find.textContaining('已选 1 项'), findsOneWidget);

    await tester.ensureVisible(find.text('开始下载'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('开始下载'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('开始下载'));
    await tester.pump();
    await tester.pumpAndSettle();
    expect(fake.lastItemIds, <int>[2]);
  });


  testWidgets('分析轮询连续失败 3 次：落到失败态并给中文提示（审查 M-1）', (tester) async {
    final fake = _FakeOfflineService(
      details: const [_analyzingDetail],
      failDetail: true,
    );
    await tester.pumpWidget(
      ProviderScope(
        overrides: [offlineDownloadServiceProvider.overrideWithValue(fake)],
        child: MaterialApp(
          theme: AppTheme.light,
          home: const OfflineNewTaskPage(),
        ),
      ),
    );
    await tester.pump();
    await tester.pump();

    await tester.enterText(find.byType(TextField), 'https://example.com/v');
    await tester.tap(find.text('分析'));
    await tester.pump();
    await tester.pump();
    // 首次 _pollDetail 立即失败 1 次，之后每 2.5s 一次，共 3 次
    for (var i = 0; i < 3; i++) {
      await tester.pump(const Duration(milliseconds: 2600));
      await tester.pump();
    }

    expect(find.textContaining('已重试 3 次'), findsOneWidget);
    expect(find.text('分析'), findsOneWidget, reason: '失败后应可重新分析');
  });

  testWidgets('非法 URL 不发请求，给中文提示', (tester) async {
    final fake = await pumpPage(tester);
    await tester.enterText(find.byType(TextField), 'not-a-url');
    await tester.tap(find.text('分析'));
    await tester.pump();
    await tester.pump();

    expect(fake.analyzeCalls, 0);
    expect(find.textContaining('http(s)'), findsWidgets);
  });
}
