import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../config/design_tokens.dart';
import '../../models/offline_task.dart';
import '../../providers/offline_provider.dart';
import '../../utils/exception.dart';
import '../../utils/logger.dart';
import '../../widgets/app_card.dart';
import '../../widgets/empty_state.dart';
import '../../widgets/error_state.dart';

/// 离线下载中心（移动端**只读**，v1.8.0）
///
/// 设计依据：`文档记录/设计文档/v1.8.0.md` §5（移动端设计） +
/// `文档记录/需求文档/v1.8.0.md` F27.5 / F30 / AC-231：
///   - 只展示任务列表与进度（状态 / 来源域名 / 产物名 / 目标 / 失败原因）；
///   - **不提供**任何创建 / 勾选 / 操作入口 —— 任务动作只在桌面端与 Web 端；
///   - 2.5s 轮询，**仅页面可见时**（切后台立即停，`dispose` 内停掉定时器）；
///   - 下拉刷新。
///
/// 只读边界（强制）：本文件不调用任何写接口，HTTP 一律经
/// `lib/services/offline_download_service.dart` → `http_service.dart`（加密信封）。
///
/// 轮询为何不随"全部终态"停止：移动端可能同时开着桌面端 / Web 端，任务由
/// 其它端创建；持续轮询才能满足 F27.4（同一账号三端在 2.5s 内看到同一份列表）。
/// 移动端设计只以"页面可见"为停止条件（§5），本页严格照此实现。
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

  /// 单页拉取条数（只读页不做分页交互，一次拉满一页即可）
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

  /// 元信息不可用时的顶部提示（只解释原因，不提供任何操作）
  String? _unavailableText() {
    if (_meta.enabled && _meta.canUse) return null;
    switch (_meta.unusableReason) {
      case 'disabled':
        return '服务端已关闭离线下载功能';
      case 'no_writable_disk':
        return '你没有可写入的磁盘，暂时无法使用离线下载';
      case 'not_configured':
        return '尚未设置离线下载目录，可在桌面端或 Web 端配置';
      case 'config_invalid':
        return '离线下载目录已失效，请在桌面端或 Web 端重新配置';
      default:
        if (!_meta.enabled) return '服务端已关闭离线下载功能';
        if (!_meta.canUse) return '离线下载当前不可用';
        return null;
    }
  }

  void _openDetail(OfflineTask task) {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => _OfflineDetailSheet(task: task),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('离线下载中心'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh_rounded),
            tooltip: '刷新',
            onPressed: _refresh,
          ),
        ],
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

    final banner = _unavailableText();
    if (_tasks.isEmpty) {
      return RefreshIndicator(
        onRefresh: _refresh,
        child: LayoutBuilder(
          builder: (context, constraints) => ListView(
            physics: const AlwaysScrollableScrollPhysics(),
            padding: const EdgeInsets.all(16),
            children: [
              if (banner != null) ...[
                _MetaBanner(text: banner),
                const SizedBox(height: 12),
              ],
              SizedBox(
                height: constraints.maxHeight * 0.7,
                child: const EmptyState(
                  icon: Icons.cloud_download_outlined,
                  title: '暂无离线下载任务',
                  subtitle: '任务在桌面端或 Web 端发起后，这里会显示进度（本页只读）',
                ),
              ),
            ],
          ),
        ),
      );
    }

    final bannerExtra = banner == null ? 0 : 1;
    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView.builder(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(12, 12, 12, 24),
        itemCount: _tasks.length + bannerExtra,
        itemBuilder: (context, index) {
          if (bannerExtra == 1 && index == 0) {
            return Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: _MetaBanner(text: banner!),
            );
          }
          final task = _tasks[index - bannerExtra];
          return _TaskCard(task: task, onTap: () => _openDetail(task));
        },
      ),
    );
  }
}

/// 元信息不可用提示条（纯展示，无按钮）
class _MetaBanner extends StatelessWidget {
  const _MetaBanner({required this.text});

  final String text;

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
        ],
      ),
    );
  }
}

/// 任务卡片（只读展示；点开进入只读详情）
class _TaskCard extends StatelessWidget {
  const _TaskCard({required this.task, required this.onTap});

  final OfflineTask task;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    final created = _formatDateTime(task.createdAt);
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
                    '${_formatSize(task.bytesDone)} / '
                    '${_formatSize(task.bytesTotal)}',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(fontSize: 12, color: t.fgMuted),
                  ),
                ),
              ] else
                const Spacer(),
              if (task.isTransferring && task.speedBps > 0)
                Text(
                  '${_formatSize(task.speedBps)}/s',
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
          // 失败原因（中文，服务端下发）
          if (task.hasError) ...[
            SizedBox(height: t.s2),
            Text(
              task.errorMessage,
              maxLines: 3,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 12, color: AppTokens.danger500),
            ),
          ],
          if (created != null) ...[
            SizedBox(height: t.s2),
            Text(
              '创建于 $created',
              style: TextStyle(fontSize: 11.5, color: t.fgSubtle),
            ),
          ],
        ],
      ),
    );
  }
}

/// 只读详情（来源域名 / 产物名 / 目标目录 / 失败原因 + 清单）
class _OfflineDetailSheet extends ConsumerStatefulWidget {
  const _OfflineDetailSheet({required this.task});

  final OfflineTask task;

  @override
  ConsumerState<_OfflineDetailSheet> createState() =>
      _OfflineDetailSheetState();
}

class _OfflineDetailSheetState extends ConsumerState<_OfflineDetailSheet> {
  OfflineTaskDetail? _detail;
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
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
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = userFacingError(e, fallback: '任务详情加载失败');
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    // 详情请求未返回前先用列表里的任务行兜底展示（不白屏）
    final task = _detail?.task ?? widget.task;
    final items = _detail?.items ?? const <OfflineItem>[];
    final created = _formatDateTime(task.createdAt);
    final started = _formatDateTime(task.startedAt);
    final finished = _formatDateTime(task.finishedAt);

    return SizedBox(
      height: MediaQuery.sizeOf(context).height * 0.82,
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
                            '${_formatSize(task.bytesDone)} / '
                            '${_formatSize(task.bytesTotal)}'
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
                // 失败原因（中文，含错误码便于排查）
                if (task.hasError)
                  _DetailRow(
                    label: '失败原因',
                    value: task.errorCode.isEmpty
                        ? task.errorMessage
                        : '${task.errorMessage}（${task.errorCode}）',
                    danger: true,
                  ),
                const Divider(height: 28),
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
                else if (_error != null) ...[
                  Text(
                    _error!,
                    style: const TextStyle(
                      fontSize: 12.5,
                      color: AppTokens.danger500,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Align(
                    alignment: Alignment.centerLeft,
                    child: OutlinedButton.icon(
                      icon: const Icon(Icons.refresh_rounded, size: 16),
                      label: const Text('重新加载'),
                      onPressed: _load,
                    ),
                  ),
                ] else if (items.isEmpty)
                  Text(
                    '该任务没有可展示的清单项',
                    style: TextStyle(fontSize: 12.5, color: t.fgMuted),
                  )
                else
                  ...items.map((item) => _ItemRow(item: item)),
                if (_detail?.hasMoreImages ?? false) ...[
                  const SizedBox(height: 8),
                  Text(
                    '该任务还有更多图片未载入',
                    style: TextStyle(fontSize: 12, color: t.fgSubtle),
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

/// 详情里的清单项行（只读）
class _ItemRow extends StatelessWidget {
  const _ItemRow({required this.item});

  final OfflineItem item;

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    final metaParts = <String>[
      if (item.ext.trim().isNotEmpty) item.ext.trim().toUpperCase(),
      if (item.resolutionText.isNotEmpty) item.resolutionText,
      if (item.durationMs > 0) _formatDuration(item.durationMs),
      if (item.estSize > 0)
        '${item.isEstimated ? '约 ' : ''}${_formatSize(item.estSize)}',
    ];
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
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

/// 字节数格式化（1024 进制，与传输/修复页口径一致）
String _formatSize(int bytes) {
  if (bytes < 1024) return '$bytes B';
  if (bytes < 1024 * 1024) {
    return '${(bytes / 1024).toStringAsFixed(1)} KB';
  }
  if (bytes < 1024 * 1024 * 1024) {
    return '${(bytes / 1024 / 1024).toStringAsFixed(1)} MB';
  }
  return '${(bytes / 1024 / 1024 / 1024).toStringAsFixed(2)} GB';
}

/// 时长格式化（毫秒 → `m:ss` / `h:mm:ss`）
String _formatDuration(int milliseconds) {
  final totalSeconds = milliseconds ~/ 1000;
  final hours = totalSeconds ~/ 3600;
  final minutes = (totalSeconds % 3600) ~/ 60;
  final seconds = totalSeconds % 60;
  final mm = minutes.toString().padLeft(2, '0');
  final ss = seconds.toString().padLeft(2, '0');
  return hours > 0 ? '$hours:$mm:$ss' : '$minutes:$ss';
}

/// ISO8601 字符串 → 本地时间 `yyyy-MM-dd HH:mm:ss`
///
/// 服务端下发的是 UTC ISO8601；解析失败时原样返回，不抛异常。
String? _formatDateTime(String? iso) {
  final value = iso?.trim() ?? '';
  if (value.isEmpty) return null;
  final parsed = DateTime.tryParse(value);
  if (parsed == null) return value;
  final local = parsed.toLocal().toString();
  return local.length >= 19 ? local.substring(0, 19) : local;
}
