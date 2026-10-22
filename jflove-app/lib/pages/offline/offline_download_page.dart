import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../config/design_tokens.dart';
import '../../config/routes.dart';
import '../../models/offline_task.dart';
import '../../providers/offline_provider.dart';
import '../../utils/exception.dart';
import '../../utils/logger.dart';
import '../../widgets/app_card.dart';
import '../../widgets/empty_state.dart';
import '../../widgets/error_state.dart';
import '../../widgets/virtual_disk_dir_picker.dart';
import 'offline_error_text.dart';
import 'offline_format.dart';
import 'offline_settings_sheet.dart';

/// 离线下载中心（移动端完整功能，v1.8.1）
///
/// 设计依据：`文档记录/设计文档/v1.8.1.md` §5 + 需求 v1.8.1 F32 / AC-248~253：
///   - 任务中心：列表 + 进度 + 新建入口 + 设置入口 + 任务操作；
///   - 详情：清单、失败原因、取消 / 重试 / 删除记录 / 追加图片页 / 去选择；
///   - 2.5s 轮询，**仅页面可见时**（切后台立即停，`dispose` 内停掉定时器）；
///   - 下拉刷新。
///
/// 约束：HTTP 一律经 `OfflineDownloadService` → `http_service.dart`（加密信封）；
/// token / session_key 不进入日志。
class OfflineDownloadPage extends ConsumerStatefulWidget {
  const OfflineDownloadPage({super.key});

  @override
  ConsumerState<OfflineDownloadPage> createState() =>
      _OfflineDownloadPageState();
}

class _OfflineDownloadPageState extends ConsumerState<OfflineDownloadPage>
    with WidgetsBindingObserver {
  /// 轮询间隔（F27.3：2.5s；移动端固定，不跟随 `meta.poll_interval_ms`）
  static const Duration _pollInterval = Duration(milliseconds: 2500);

  /// 单页拉取条数（移动端一次拉满一页即可）
  static const int _pageSize = 50;

  List<OfflineTask> _tasks = const [];
  OfflineMeta _meta = OfflineMeta.empty;
  bool _loading = true;

  /// 请求进行中标记：避免轮询与下拉刷新叠加请求
  bool _inFlight = false;
  String? _error;
  Timer? _pollTimer;

  /// 页面是否可见（生命周期 resumed）；不可见时**不**轮询（AC-232）
  bool _pageVisible = true;

  @override
  void initState() {
    super.initState();
    // 页面可见性守卫：切后台停止轮询，回前台恢复并立即刷新一次
    WidgetsBinding.instance.addObserver(this);
    _load(includeMeta: true);
    _startPolling();
  }

  @override
  void dispose() {
    // 必须停掉定时器，否则页面关闭后仍在后台发请求（AC-232）
    _stopPolling();
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    final visible = state == AppLifecycleState.resumed;
    if (visible == _pageVisible) return;
    _pageVisible = visible;
    if (visible) {
      _startPolling();
      // 回前台先补一次（后台期间可能已有新任务/新进度）
      _load(includeMeta: true);
    } else {
      _stopPolling();
    }
  }

  void _startPolling() {
    if (_pollTimer != null || !_pageVisible) return;
    _pollTimer = Timer.periodic(_pollInterval, (_) => _load());
  }

  void _stopPolling() {
    _pollTimer?.cancel();
    _pollTimer = null;
  }

  /// 拉取任务列表（`includeMeta=true` 时同时刷新元信息）
  ///
  /// 静默失败策略：轮询失败不打断已有列表，只在首屏无数据时落到错误态。
  Future<void> _load({bool includeMeta = false}) async {
    if (_inFlight) return;
    _inFlight = true;
    try {
      final service = ref.read(offlineDownloadServiceProvider);
      final list = await service.tasks(page: 1, pageSize: _pageSize);
      OfflineMeta? meta;
      if (includeMeta) {
        try {
          meta = await service.meta();
        } catch (e) {
          // 元信息只驱动顶部提示，失败不影响任务列表
          log.fine('离线下载元信息拉取失败：${userFacingError(e)}');
        }
      }
      if (!mounted) return;
      setState(() {
        _tasks = list.tasks;
        if (meta != null) _meta = meta;
        _loading = false;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      final message = userFacingError(e, fallback: '离线下载任务加载失败');
      if (_tasks.isEmpty) {
        setState(() {
          _loading = false;
          _error = message;
        });
      } else {
        log.fine('离线下载任务轮询失败：$message');
      }
    } finally {
      _inFlight = false;
    }
  }

  /// 下拉刷新 / 顶部刷新（元信息一并更新）
  Future<void> _refresh() => _load(includeMeta: true);

  /// 元信息不可用时的顶部提示（不可用原因 + 是否引导去设置）
  ({String text, bool needsSettings})? _unavailableNotice() {
    if (_meta.enabled && _meta.canUse && _meta.configured && _meta.configValid) {
      return null;
    }
    switch (_meta.unusableReason) {
      case 'disabled':
        return (text: '服务端已关闭离线下载功能', needsSettings: false);
      case 'no_writable_disk':
        return (
          text: '你没有可写入的磁盘，暂时无法使用离线下载',
          needsSettings: false,
        );
      case 'not_configured':
        return (text: '尚未设置离线下载默认目录', needsSettings: true);
      case 'config_invalid':
        return (text: '离线下载默认目录已失效，请重新配置', needsSettings: true);
      default:
        if (!_meta.enabled) {
          return (text: '服务端已关闭离线下载功能', needsSettings: false);
        }
        if (!_meta.canUse) {
          return (text: '离线下载当前不可用', needsSettings: false);
        }
        if (!_meta.configured || !_meta.configValid) {
          return (text: '尚未配置可用的默认目录', needsSettings: true);
        }
        return null;
    }
  }

  Future<void> _openNewTask() async {
    // MaterialApp.router + GoRouter：必须用 go_router 的 push，不能用 Navigator.pushNamed
    final created = await context.push<bool>(AppRoutes.offlineNew);
    if (!mounted) return;
    if (created == true) {
      await _refresh();
    }
  }

  Future<void> _openSettings() async {
    await showOfflineSettingsSheet(
      context,
      meta: _meta,
      onSaved: _refresh,
    );
  }

  /// 统一的任务动作：成功后刷新列表 + 用中文提示失败原因
  Future<void> _runTaskAction(
    Future<void> Function() action,
    String successText,
  ) async {
    try {
      await action();
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(successText)));
      await _refresh();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(offlineOperationError(e, fallback: '操作失败')),
        ),
      );
    }
  }

  void _openDetail(OfflineTask task) {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => _OfflineDetailSheet(
        task: task,
        meta: _meta,
        onChanged: _refresh,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('离线下载中心'),
        actions: [
          IconButton(
            icon: const Icon(Icons.settings_outlined),
            tooltip: '设置',
            onPressed: _openSettings,
          ),
          IconButton(
            icon: const Icon(Icons.refresh_rounded),
            tooltip: '刷新',
            onPressed: _refresh,
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _openNewTask,
        icon: const Icon(Icons.add),
        label: const Text('新建任务'),
      ),
      body: _buildBody(context),
    );
  }

  Widget _buildBody(BuildContext context) {
    if (_loading && _tasks.isEmpty) {
      return const Padding(
        padding: EdgeInsets.all(16),
        child: ListSkeleton(rows: 4),
      );
    }
    if (_error != null && _tasks.isEmpty) {
      return ErrorState(
        title: '任务加载失败',
        message: _error!,
        onRetry: _refresh,
        retryLabel: '重新加载',
      );
    }

    final notice = _unavailableNotice();
    final bannerExtra = notice == null ? 0 : 1;
    if (_tasks.isEmpty) {
      return RefreshIndicator(
        onRefresh: _refresh,
        child: LayoutBuilder(
          builder: (context, constraints) => ListView(
            physics: const AlwaysScrollableScrollPhysics(),
            padding: const EdgeInsets.all(16),
            children: [
              if (notice != null) ...[
                _MetaBanner(
                  text: notice.text,
                  onSettings: notice.needsSettings ? _openSettings : null,
                ),
                const SizedBox(height: 12),
              ],
              SizedBox(
                height: constraints.maxHeight * 0.7,
                child: EmptyState(
                  icon: Icons.cloud_download_outlined,
                  title: '暂无离线下载任务',
                  subtitle: '点右下角「新建任务」发起，或等桌面端 / Web 端创建后同步显示',
                  action: FilledButton.icon(
                    icon: const Icon(Icons.add, size: 18),
                    label: const Text('新建任务'),
                    onPressed: _openNewTask,
                  ),
                ),
              ),
            ],
          ),
        ),
      );
    }

    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView.builder(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(12, 12, 12, 88),
        itemCount: _tasks.length + bannerExtra,
        itemBuilder: (context, index) {
          if (bannerExtra == 1 && index == 0) {
            return Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: _MetaBanner(
                text: notice!.text,
                onSettings: notice.needsSettings ? _openSettings : null,
              ),
            );
          }
          final task = _tasks[index - bannerExtra];
          return _TaskCard(
            task: task,
            onTap: () => _openDetail(task),
            onCancel: () => _runTaskAction(
              () => ref.read(offlineDownloadServiceProvider).cancel(task.id),
              '任务已取消',
            ),
            onRetry: () => _runTaskAction(
              () => ref.read(offlineDownloadServiceProvider).retry(task.id),
              '已重新提交',
            ),
            onDelete: () => _runTaskAction(
              () => ref
                  .read(offlineDownloadServiceProvider)
                  .deleteRecord(task.id),
              '任务记录已删除',
            ),
            onSelect: () => _openDetail(task),
          );
        },
      ),
    );
  }
}

/// 元信息不可用提示条（可带「去设置」）
class _MetaBanner extends StatelessWidget {
  const _MetaBanner({required this.text, this.onSettings});

  final String text;
  final VoidCallback? onSettings;

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: AppTokens.warning500.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(t.rMd),
        border: Border.all(color: AppTokens.warning500.withValues(alpha: 0.35)),
      ),
      child: Row(
        children: [
          const Icon(
            Icons.info_outline_rounded,
            size: 16,
            color: AppTokens.warning500,
          ),
          SizedBox(width: t.s2),
          Expanded(
            child: Text(
              text,
              style: TextStyle(fontSize: 12, color: t.fgDefault, height: 1.4),
            ),
          ),
          if (onSettings != null)
            TextButton(onPressed: onSettings, child: const Text('去设置')),
        ],
      ),
    );
  }
}

/// 任务卡片（展示 + 操作入口）
class _TaskCard extends StatelessWidget {
  const _TaskCard({
    required this.task,
    required this.onTap,
    required this.onCancel,
    required this.onRetry,
    required this.onDelete,
    required this.onSelect,
  });

  final OfflineTask task;
  final VoidCallback onTap;
  final VoidCallback onCancel;
  final VoidCallback onRetry;
  final VoidCallback onDelete;
  final VoidCallback onSelect;

  bool get _canCancel => const {
    'queued',
    'analyzing',
    'downloading',
    'merging',
  }.contains(task.status);

  bool get _canRetry =>
      (task.status == 'failed' || task.status == 'canceled') &&
      canRetryErrorCode(task.errorCode);

  bool get _canDelete => task.isTerminal || task.status == 'ready';

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    final created = formatDateTime(task.createdAt);
    return AppCard(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(14),
      onTap: onTap,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 产物名 + 状态徽标
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(
                Icons.download_for_offline_outlined,
                size: 18,
                color: t.fgMuted,
              ),
              SizedBox(width: t.s2),
              Expanded(
                child: Text(
                  task.productName,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.w600,
                    color: t.fgDefault,
                  ),
                ),
              ),
              SizedBox(width: t.s2),
              AppBadge(task.statusLabel, tone: _statusTone(task.status)),
            ],
          ),
          SizedBox(height: t.s2),
          // 来源域名（服务端已脱敏，只给域名）
          Text(
            '来源 ${task.sourceDomain.isEmpty ? '未知站点' : task.sourceDomain}',
            style: TextStyle(fontSize: 12, color: t.fgMuted),
          ),
          SizedBox(height: t.s3),
          // 进度条 + 百分比 + 字节/速度
          ClipRRect(
            borderRadius: BorderRadius.circular(999),
            child: LinearProgressIndicator(
              value: task.progress,
              minHeight: 6,
              backgroundColor: t.bgSunken,
              valueColor: const AlwaysStoppedAnimation<Color>(
                AppTokens.brand500,
              ),
            ),
          ),
          SizedBox(height: 6),
          Row(
            children: [
              Text(
                '${task.progressPercent}%',
                style: TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                  color: t.fgDefault,
                ),
              ),
              if (task.bytesTotal > 0) ...[
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    '${formatSize(task.bytesDone)} / '
                    '${formatSize(task.bytesTotal)}',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(fontSize: 12, color: t.fgMuted),
                  ),
                ),
              ] else
                const Spacer(),
              if (task.isTransferring && task.speedBps > 0)
                Text(
                  '${formatSize(task.speedBps)}/s',
                  style: TextStyle(fontSize: 12, color: t.fgMuted),
                ),
            ],
          ),
          if (task.itemTotal > 0) ...[
            SizedBox(height: t.s1),
            Text(
              '产物项 ${task.itemDone}/${task.itemTotal}',
              style: TextStyle(fontSize: 12, color: t.fgMuted),
            ),
          ],
          SizedBox(height: t.s1),
          Text(
            '目标 ${task.targetDisplay}',
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(fontSize: 12, color: t.fgMuted),
          ),
          // 失败原因（中文，服务端下发 + 本地建议）
          if (task.hasError) ...[
            SizedBox(height: t.s2),
            Text(
              offlineErrorText(task.errorCode, task.errorMessage),
              maxLines: 4,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 12.5, color: AppTokens.danger500),
            ),
          ],
          if (created != null) ...[
            SizedBox(height: t.s2),
            Text(
              '创建于 $created',
              style: TextStyle(fontSize: 11.5, color: t.fgSubtle),
            ),
          ],
          // 操作入口（v1.8.1）
          if (_canCancel || _canRetry || _canDelete || task.status == 'ready')
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Wrap(
                spacing: 8,
                runSpacing: 4,
                children: [
                  if (task.status == 'ready')
                    TextButton(onPressed: onSelect, child: const Text('去选择')),
                  if (_canCancel)
                    TextButton(onPressed: onCancel, child: const Text('取消')),
                  if (_canRetry)
                    TextButton(onPressed: onRetry, child: const Text('重试')),
                  if (_canDelete)
                    TextButton(
                      onPressed: onDelete,
                      style: TextButton.styleFrom(
                        foregroundColor: AppTokens.danger500,
                      ),
                      child: const Text('删除记录'),
                    ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

/// 详情 / 勾选弹层
class _OfflineDetailSheet extends ConsumerStatefulWidget {
  const _OfflineDetailSheet({
    required this.task,
    required this.meta,
    required this.onChanged,
  });

  final OfflineTask task;
  final OfflineMeta meta;
  final Future<void> Function() onChanged;

  @override
  ConsumerState<_OfflineDetailSheet> createState() =>
      _OfflineDetailSheetState();
}

class _OfflineDetailSheetState extends ConsumerState<_OfflineDetailSheet> {
  OfflineTaskDetail? _detail;
  bool _loading = true;
  bool _busy = false;
  String? _error;
  final Set<int> _selected = <int>{};
  int _targetDiskId = 0;
  String _targetPath = '';
  bool _packZip = false;

  OfflineTask get _task => _detail?.task ?? widget.task;

  @override
  void initState() {
    super.initState();
    _targetDiskId = widget.meta.configDiskId;
    _targetPath = widget.meta.configRelPath;
    _load();
  }

  Future<void> _load() async {
    if (!_loading) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      final detail = await ref
          .read(offlineDownloadServiceProvider)
          .detail(widget.task.id);
      if (!mounted) return;
      setState(() {
        _detail = detail;
        _loading = false;
        _error = null;
        if (detail.task.status == 'ready') {
          _selected
            ..clear()
            ..addAll(detail.items.map((item) => item.id));
        }
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = offlineOperationError(e, fallback: '任务详情加载失败');
      });
    }
  }

  Future<void> _run(Future<void> Function() action, String successText) async {
    setState(() => _busy = true);
    try {
      await action();
      await widget.onChanged();
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(successText)));
      Navigator.of(context).pop();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = offlineOperationError(e, fallback: '操作失败');
      });
    }
  }

  Future<void> _nextPage() async {
    setState(() => _busy = true);
    try {
      final result = await ref
          .read(offlineDownloadServiceProvider)
          .nextPage(widget.task.id);
      await _load();
      await widget.onChanged();
      if (!mounted) return;
      setState(() => _busy = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('已追加 ${result.added} 张图片')),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = offlineOperationError(e, fallback: '追加下一页失败');
      });
    }
  }

  Future<void> _pickTarget() async {
    final disks = widget.meta.writableDisks;
    if (disks.isEmpty) return;
    final picked = await VirtualDiskDirPicker.show(
      context,
      disks: disks
          .map((d) => DirPickerDisk(id: d.diskId, name: d.name))
          .toList(growable: false),
      initialDiskId: _targetDiskId,
      initialPath: _targetPath,
      title: '选择目标目录',
    );
    if (picked == null || !mounted) return;
    setState(() {
      _targetDiskId = picked.diskId;
      _targetPath = picked.path;
    });
  }

  Future<void> _submit() async {
    if (_selected.isEmpty) {
      setState(() => _error = '请至少勾选一项');
      return;
    }
    if (_targetDiskId <= 0) {
      setState(() => _error = '请选择目标目录');
      return;
    }
    await _run(
      () => ref
          .read(offlineDownloadServiceProvider)
          .select(
            taskId: widget.task.id,
            itemIds: _selected.toList(growable: false),
            diskId: _targetDiskId,
            targetPath: _targetPath,
            packZip: _packZip,
          ),
      '已开始下载',
    );
  }

  String _targetLabel() {
    var diskName = '';
    for (final disk in widget.meta.writableDisks) {
      if (disk.diskId == _targetDiskId) {
        diskName = disk.name;
        break;
      }
    }
    if (diskName.isEmpty) return '未选择';
    return '$diskName / ${_targetPath.isEmpty ? '磁盘根目录' : _targetPath}';
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    final task = _task;
    final items = _detail?.items ?? const <OfflineItem>[];
    final ready = task.status == 'ready';
    final created = formatDateTime(task.createdAt);
    final started = formatDateTime(task.startedAt);
    final finished = formatDateTime(task.finishedAt);

    return SizedBox(
      height: MediaQuery.sizeOf(context).height * 0.86,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 8, 4),
            child: Row(
              children: [
                Expanded(
                  child: Text(
                    '任务详情',
                    style: TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.w600,
                      color: t.fgDefault,
                    ),
                  ),
                ),
                IconButton(
                  icon: const Icon(Icons.close_rounded),
                  tooltip: '关闭',
                  onPressed: () => Navigator.of(context).pop(),
                ),
              ],
            ),
          ),
          Expanded(
            child: ListView(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 24),
              children: [
                _DetailRow(
                  label: '状态',
                  value: task.statusLabel.isEmpty ? '—' : task.statusLabel,
                ),
                _DetailRow(
                  label: '来源域名',
                  value: task.sourceDomain.isEmpty ? '未知站点' : task.sourceDomain,
                ),
                _DetailRow(label: '产物名', value: task.productName),
                _DetailRow(label: '目标目录', value: task.targetDisplay),
                _DetailRow(
                  label: '进度',
                  value: task.bytesTotal > 0
                      ? '${task.progressPercent}% · '
                            '${formatSize(task.bytesDone)} / '
                            '${formatSize(task.bytesTotal)}'
                      : '${task.progressPercent}%',
                ),
                if (task.itemTotal > 0)
                  _DetailRow(
                    label: '产物项',
                    value: '${task.itemDone}/${task.itemTotal}',
                  ),
                if (created != null) _DetailRow(label: '创建时间', value: created),
                if (started != null) _DetailRow(label: '开始时间', value: started),
                if (finished != null)
                  _DetailRow(label: '完成时间', value: finished),
                if (task.hasError)
                  _DetailRow(
                    label: '失败原因',
                    value: offlineErrorText(
                      task.errorCode,
                      task.errorMessage,
                    ),
                    danger: true,
                  ),
                if (_error != null) ...[
                  const SizedBox(height: 8),
                  Text(
                    _error!,
                    style: const TextStyle(
                      fontSize: 12.5,
                      height: 1.4,
                      color: AppTokens.danger500,
                    ),
                  ),
                ],
                if (ready) ...[
                  const Divider(height: 24),
                  Text(
                    '选择要下载的项（已全选，可取消勾选）',
                    style: TextStyle(fontSize: 12.5, color: t.fgMuted),
                  ),
                  const SizedBox(height: 4),
                ],
                const Divider(height: 24),
                Row(
                  children: [
                    Text(
                      '清单',
                      style: TextStyle(
                        fontSize: 14,
                        fontWeight: FontWeight.w600,
                        color: t.fgDefault,
                      ),
                    ),
                    SizedBox(width: t.s2),
                    if (items.isNotEmpty)
                      Text(
                        '${items.length} 项',
                        style: TextStyle(fontSize: 12, color: t.fgMuted),
                      ),
                  ],
                ),
                const SizedBox(height: 10),
                if (_loading)
                  const _ItemsSkeleton()
                else if (_error != null && items.isEmpty)
                  Align(
                    alignment: Alignment.centerLeft,
                    child: OutlinedButton.icon(
                      icon: const Icon(Icons.refresh_rounded, size: 16),
                      label: const Text('重新加载'),
                      onPressed: _load,
                    ),
                  )
                else if (items.isEmpty)
                  Text(
                    '该任务没有可展示的清单项',
                    style: TextStyle(fontSize: 12.5, color: t.fgMuted),
                  )
                else
                  ...items.map(
                    (item) => ready
                        ? Material(
                            color: Colors.transparent,
                            child: CheckboxListTile(
                              contentPadding: EdgeInsets.zero,
                              dense: true,
                              value: _selected.contains(item.id),
                              onChanged: _busy
                                  ? null
                                  : (checked) {
                                      setState(() {
                                        if (checked == true) {
                                          _selected.add(item.id);
                                        } else {
                                          _selected.remove(item.id);
                                        }
                                      });
                                    },
                              title: _ItemRow(item: item),
                            ),
                          )
                        : _ItemRow(item: item),
                  ),
                if (ready) ...[
                  const SizedBox(height: 8),
                  Row(
                    children: [
                      Expanded(
                        child: Text(
                          '目标：${_targetLabel()}',
                          style: TextStyle(fontSize: 12, color: t.fgMuted),
                        ),
                      ),
                      OutlinedButton.icon(
                        icon: const Icon(Icons.folder_open_outlined, size: 16),
                        label: const Text('选择'),
                        onPressed: _busy ? null : _pickTarget,
                      ),
                    ],
                  ),
                  Material(
                    color: Colors.transparent,
                    child: SwitchListTile(
                      contentPadding: EdgeInsets.zero,
                      dense: true,
                      title: const Text('打包为 zip'),
                      value: _packZip,
                      onChanged: _busy
                          ? null
                          : (v) => setState(() => _packZip = v),
                    ),
                  ),
                  SizedBox(
                    width: double.infinity,
                    child: FilledButton.icon(
                      icon: const Icon(Icons.download, size: 18),
                      label: const Text('开始下载'),
                      onPressed: _busy ? null : _submit,
                    ),
                  ),
                ],
                if ((_detail?.hasMoreImages ?? false) && ready) ...[
                  const SizedBox(height: 8),
                  Align(
                    alignment: Alignment.centerLeft,
                    child: OutlinedButton.icon(
                      icon: const Icon(Icons.add_photo_alternate_outlined),
                      label: const Text('追加下一页'),
                      onPressed: _busy ? null : _nextPage,
                    ),
                  ),
                ],
                const SizedBox(height: 12),
                Wrap(
                  spacing: 8,
                  runSpacing: 4,
                  children: [
                    if (const {
                      'queued',
                      'analyzing',
                      'downloading',
                      'merging',
                    }.contains(task.status))
                      TextButton.icon(
                        icon: const Icon(Icons.close, size: 16),
                        label: const Text('取消任务'),
                        onPressed: _busy
                            ? null
                            : () => _run(
                                () => ref
                                    .read(offlineDownloadServiceProvider)
                                    .cancel(task.id),
                                '任务已取消',
                              ),
                      ),
                    if ((task.status == 'failed' || task.status == 'canceled') &&
                        canRetryErrorCode(task.errorCode))
                      TextButton.icon(
                        icon: const Icon(Icons.refresh, size: 16),
                        label: const Text('重试'),
                        onPressed: _busy
                            ? null
                            : () => _run(
                                () => ref
                                    .read(offlineDownloadServiceProvider)
                                    .retry(task.id),
                                '已重新提交',
                              ),
                      ),
                    if (task.isTerminal || ready)
                      TextButton.icon(
                        icon: const Icon(Icons.delete_outline, size: 16),
                        label: const Text('删除记录'),
                        style: TextButton.styleFrom(
                          foregroundColor: AppTokens.danger500,
                        ),
                        onPressed: _busy
                            ? null
                            : () => _run(
                                () => ref
                                    .read(offlineDownloadServiceProvider)
                                    .deleteRecord(task.id),
                                '任务记录已删除',
                              ),
                      ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// 详情里的「标签：值」行
class _DetailRow extends StatelessWidget {
  const _DetailRow({
    required this.label,
    required this.value,
    this.danger = false,
  });

  final String label;
  final String value;
  final bool danger;

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 72,
            child: Text(
              label,
              style: TextStyle(fontSize: 12.5, color: t.fgMuted),
            ),
          ),
          Expanded(
            child: Text(
              value,
              style: TextStyle(
                fontSize: 13,
                height: 1.4,
                color: danger ? AppTokens.danger500 : t.fgDefault,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// 详情清单加载骨架
///
/// 不能直接用 [ListSkeleton]：它本身是一个 `ListView`，嵌在详情的外层
/// `ListView` 里会拿到无界高度并抛
/// `Vertical viewport was given unbounded height`（实测踩到）。
class _ItemsSkeleton extends StatelessWidget {
  const _ItemsSkeleton();

  @override
  Widget build(BuildContext context) {
    return Column(
      children: List.generate(
        3,
        (_) => const Padding(
          padding: EdgeInsets.only(bottom: 12),
          child: Row(
            children: [
              SkeletonBox(width: 16, height: 16, radius: 4),
              SizedBox(width: 8),
              Expanded(child: SkeletonBox(height: 12)),
            ],
          ),
        ),
      ),
    );
  }
}

/// 详情里的清单项行
class _ItemRow extends StatelessWidget {
  const _ItemRow({required this.item});

  final OfflineItem item;

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    final metaParts = <String>[
      if (item.ext.trim().isNotEmpty) item.ext.trim().toUpperCase(),
      if (item.resolutionText.isNotEmpty) item.resolutionText,
      if (item.durationMs > 0) formatDuration(item.durationMs),
      if (item.estSize > 0)
        '${item.isEstimated ? '约 ' : ''}${formatSize(item.estSize)}',
    ];
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(_kindIcon(item.kind), size: 16, color: t.fgMuted),
          SizedBox(width: t.s2),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  item.title,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 13,
                    color: t.fgDefault,
                    height: 1.35,
                  ),
                ),
                if (metaParts.isNotEmpty) ...[
                  const SizedBox(height: 2),
                  Text(
                    metaParts.join(' · '),
                    style: TextStyle(fontSize: 11.5, color: t.fgMuted),
                  ),
                ],
                if (item.errorMessage.trim().isNotEmpty) ...[
                  const SizedBox(height: 2),
                  Text(
                    item.errorMessage,
                    style: const TextStyle(
                      fontSize: 11.5,
                      color: AppTokens.danger500,
                    ),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}

// ── 展示辅助函数（无网络、无状态）─────────────────────────────────

/// 任务 / 清单项状态 → 徽标色调
BadgeTone _statusTone(String status) {
  switch (status) {
    case 'success':
      return BadgeTone.success;
    case 'failed':
      return BadgeTone.danger;
    case 'downloading':
    case 'merging':
      return BadgeTone.brand;
    case 'queued':
    case 'analyzing':
    case 'ready':
      return BadgeTone.warning;
    default:
      return BadgeTone.neutral;
  }
}

/// 清单项类型 → 图标
IconData _kindIcon(String kind) {
  switch (kind) {
    case 'video':
      return Icons.movie_outlined;
    case 'audio':
      return Icons.music_note_outlined;
    case 'image':
      return Icons.image_outlined;
    case 'subtitle':
      return Icons.subtitles_outlined;
    default:
      return Icons.insert_drive_file_outlined;
  }
}
