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
import '../../widgets/virtual_disk_dir_picker.dart';
import 'offline_error_text.dart';
import 'offline_format.dart';
import 'offline_settings_sheet.dart';

/// 新建离线下载任务（v1.8.1 / F32.2、AC-248）
///
/// 流程：粘贴网址 → 分析（2.5s 轮询详情）→ 勾选清单 → 选目标目录 → 开始下载。
///
/// 约束：
///   - 所有请求走 `OfflineDownloadService` → `HttpService` 加密信封；
///   - 默认目录未配置 / 无写权限磁盘时**不发起 analyze**，只给中文说明与「去设置」；
///   - 页面不可见时停止轮询（生命周期监听）；
///   - 失败展示中文原因与建议，不显示 Dio 原始异常。
class OfflineNewTaskPage extends ConsumerStatefulWidget {
  const OfflineNewTaskPage({super.key});

  @override
  ConsumerState<OfflineNewTaskPage> createState() => _OfflineNewTaskPageState();
}

enum _Phase { idle, analyzing, ready, submitting, failed }

class _OfflineNewTaskPageState extends ConsumerState<OfflineNewTaskPage>
    with WidgetsBindingObserver {
  /// 轮询退避（C2）：2.5s → 5s → 10s → 15s，封顶 30s
  static const List<int> _pollBackoffMs = [2500, 5000, 10000, 15000, 30000];

  final TextEditingController _urlController = TextEditingController();

  OfflineMeta _meta = OfflineMeta.empty;
  bool _metaLoading = true;
  String? _metaError;

  _Phase _phase = _Phase.idle;
  int _taskId = 0;
  OfflineTaskDetail? _detail;
  final Set<int> _selected = <int>{};
  bool _packZip = false;
  String? _errorText;
  Timer? _pollTimer;

  /// 连续轮询失败次数（达到 3 次转失败态，避免页面永久停在「分析中」）
  int _pollFailures = 0;

  /// 当前退避档位（每次轮询后 +1，封顶到 30s）
  int _pollTick = 0;

  int _targetDiskId = 0;
  String _targetPath = '';

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _loadMeta();
  }

  @override
  void dispose() {
    _stopPolling();
    WidgetsBinding.instance.removeObserver(this);
    _urlController.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      // 回前台：分析中立即补一次并恢复退避轮询（后台期间定时器已停）
      if (_phase == _Phase.analyzing) {
        // 回前台立即补一次（后台期间定时器已停），后续按当前档位退避
        _pollDetail();
      }
    } else {
      _stopPolling();
    }
  }

  bool get _canAnalyze =>
      _meta.enabled && _meta.canUse && _meta.configured && _meta.configValid;

  /// 默认目录是否可用（保存配置后可能变化）
  String? get _unavailableReason {
    if (!_meta.enabled) return '服务端已关闭离线下载功能';
    if (!_meta.canUse) return '你没有可写入的磁盘，暂时无法使用离线下载';
    if (!_meta.configured) return '尚未设置离线下载默认目录';
    if (!_meta.configValid) return '离线下载默认目录已失效，请重新配置';
    return null;
  }

  Future<void> _loadMeta() async {
    setState(() {
      _metaLoading = true;
      _metaError = null;
    });
    try {
      final meta = await ref.read(offlineDownloadServiceProvider).meta();
      if (!mounted) return;
      setState(() {
        _meta = meta;
        _metaLoading = false;
        if (meta.configDiskId > 0) _targetDiskId = meta.configDiskId;
        if (meta.configRelPath.isNotEmpty) _targetPath = meta.configRelPath;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _metaLoading = false;
        _metaError = userFacingError(e, fallback: '离线下载配置加载失败');
      });
    }
  }

  Future<void> _openSettings() async {
    await showOfflineSettingsSheet(context, meta: _meta, onSaved: _loadMeta);
  }

  String? _validateUrl(String value) {
    final url = value.trim();
    if (url.isEmpty) return '请输入网页地址';
    final uri = Uri.tryParse(url);
    if (uri == null ||
        !(uri.scheme == 'http' || uri.scheme == 'https') ||
        uri.host.isEmpty) {
      return '请输入 http(s) 开头的公开网页地址';
    }
    return null;
  }

  Future<void> _analyze() async {
    final invalid = _validateUrl(_urlController.text);
    if (invalid != null) {
      setState(() => _errorText = invalid);
      return;
    }
    if (!_canAnalyze) {
      setState(() => _errorText = _unavailableReason ?? '当前不可用');
      return;
    }
    setState(() {
      _phase = _Phase.analyzing;
      _errorText = null;
      _detail = null;
      _selected.clear();
      _taskId = 0;
      _pollFailures = 0;
      _pollTick = 0;
    });
    try {
      final result = await ref
          .read(offlineDownloadServiceProvider)
          .analyze(_urlController.text.trim());
      if (!mounted) return;
      setState(() => _taskId = result.taskId);
      // 立即查一次详情；后续间隔由 _scheduleNextPoll 按退避档位安排
      _pollDetail();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _phase = _Phase.failed;
        _errorText = offlineOperationError(e, fallback: '分析失败，请重试');
      });
    }
  }

  /// 按当前退避档位安排下一次轮询（单次 Timer，避免固定周期）
  void _scheduleNextPoll() {
    _stopPolling();
    final index = _pollTick < _pollBackoffMs.length
        ? _pollTick
        : _pollBackoffMs.length - 1;
    _pollTick++;
    _pollTimer = Timer(
      Duration(milliseconds: _pollBackoffMs[index]),
      _pollDetail,
    );
  }

  void _stopPolling() {
    _pollTimer?.cancel();
    _pollTimer = null;
  }

  Future<void> _pollDetail() async {
    if (_taskId <= 0) return;
    try {
      final detail = await ref
          .read(offlineDownloadServiceProvider)
          .detail(_taskId);
      if (!mounted) return;
      _pollFailures = 0;
      final status = detail.task.status;
      if (status == 'ready') {
        _stopPolling();
        setState(() {
          _phase = _Phase.ready;
          _detail = detail;
          _selected
            ..clear()
            ..addAll(detail.items.map((item) => item.id));
          _errorText = null;
        });
      } else if (status == 'failed' || status == 'canceled') {
        _stopPolling();
        setState(() {
          _phase = _Phase.failed;
          _detail = detail;
          _errorText = offlineErrorText(
            detail.task.errorCode,
            detail.task.errorMessage,
          );
        });
      } else {
        // queued / analyzing：继续等（按退避档位安排下一次）
        if (mounted) setState(() => _detail = detail);
        _scheduleNextPoll();
      }
    } catch (e) {
      if (!mounted) return;
      _pollFailures++;
      final message = userFacingError(e, fallback: '分析状态获取失败');
      log.fine('离线下载分析轮询失败（$_pollFailures/3）：$message');
      if (_pollFailures >= 3) {
        _stopPolling();
        setState(() {
          _phase = _Phase.failed;
          _errorText = '$message（已重试 3 次，请检查网络后重新分析）';
        });
      } else {
        _scheduleNextPoll();
      }
    }
  }

  Future<void> _cancelAnalyze() async {
    _stopPolling();
    final taskId = _taskId;
    setState(() {
      _phase = _Phase.idle;
      _detail = null;
      _errorText = null;
      _taskId = 0;
    });
    if (taskId > 0) {
      try {
        await ref.read(offlineDownloadServiceProvider).cancel(taskId);
      } catch (e) {
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(offlineOperationError(e, fallback: '取消分析失败'))),
        );
      }
    }
  }

  Future<void> _pickTarget() async {
    final disks = _meta.writableDisks;
    if (disks.isEmpty) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('没有可写入的磁盘')));
      return;
    }
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
      setState(() => _errorText = '请至少勾选一项');
      return;
    }
    if (_targetDiskId <= 0) {
      setState(() => _errorText = '请选择目标目录');
      return;
    }
    setState(() {
      _phase = _Phase.submitting;
      _errorText = null;
    });
    try {
      await ref
          .read(offlineDownloadServiceProvider)
          .select(
            taskId: _taskId,
            itemIds: _selected.toList(growable: false),
            diskId: _targetDiskId,
            targetPath: _targetPath,
            packZip: _packZip,
          );
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _phase = _Phase.ready;
        _errorText = offlineOperationError(e, fallback: '开始下载失败');
      });
    }
  }

  // ── 快捷勾选 ──────────────────────────────────────────────

  List<OfflineItem> get _items => _detail?.items ?? const <OfflineItem>[];

  void _selectAll() {
    setState(() {
      _selected
        ..clear()
        ..addAll(_items.map((item) => item.id));
    });
  }

  void _selectByKind(String kind) {
    final ids = _items
        .where((item) => item.kind == kind)
        .map((item) => item.id)
        .toList(growable: false);
    setState(() {
      _selected
        ..clear()
        ..addAll(ids);
      if (ids.isEmpty) _errorText = '清单里没有该类型的项';
    });
  }

  void _selectHighest() {
    final videos = _items.where((item) => item.kind == 'video').toList();
    final pool = videos.isNotEmpty ? videos : _items;
    if (pool.isEmpty) return;
    final sorted = [...pool]
      ..sort((a, b) {
        final areaA = a.width * a.height;
        final areaB = b.width * b.height;
        if (areaA != areaB) return areaB.compareTo(areaA);
        if (a.estSize != b.estSize) return b.estSize.compareTo(a.estSize);
        return a.id.compareTo(b.id);
      });
    setState(() {
      _selected
        ..clear()
        ..add(sorted.first.id);
    });
  }

  int get _selectedBytes => _items
      .where((item) => _selected.contains(item.id))
      .fold(0, (sum, item) => sum + item.estSize);

  // ── UI ──────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('新建离线下载任务')),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    final t = context.tokens;
    if (_metaLoading) {
      return const Padding(
        padding: EdgeInsets.all(16),
        child: ListSkeleton(rows: 3),
      );
    }
    if (_metaError != null) {
      return ErrorState(
        title: '配置加载失败',
        message: _metaError!,
        onRetry: _loadMeta,
        retryLabel: '重新加载',
      );
    }

    final unavailable = _unavailableReason;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        if (unavailable != null) ...[
          _NoticeCard(
            text: unavailable,
            actionLabel: '去设置',
            onAction: _openSettings,
          ),
          const SizedBox(height: 12),
        ],
        AppCard(
          padding: const EdgeInsets.all(14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                '网页地址',
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                  color: t.fgDefault,
                ),
              ),
              const SizedBox(height: 8),
              TextField(
                controller: _urlController,
                enabled: _phase == _Phase.idle || _phase == _Phase.failed,
                keyboardType: TextInputType.url,
                decoration: const InputDecoration(
                  hintText: 'https://example.com/video',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 10),
              Row(
                children: [
                  Expanded(
                    child: Text(
                      '仅支持公网 http(s) 页面；不支持登录态站点 / DRM / 直播。',
                      style: TextStyle(fontSize: 11.5, color: t.fgMuted),
                    ),
                  ),
                  const SizedBox(width: 8),
                  if (_phase == _Phase.analyzing)
                    OutlinedButton(
                      onPressed: _cancelAnalyze,
                      child: const Text('取消'),
                    )
                  else
                    FilledButton.icon(
                      icon: const Icon(Icons.search, size: 18),
                      label: const Text('分析'),
                      onPressed: _canAnalyze ? _analyze : null,
                    ),
                ],
              ),
              if (_phase == _Phase.analyzing) ...[
                const SizedBox(height: 12),
                const LinearProgressIndicator(),
                const SizedBox(height: 6),
                Text(
                  '正在分析页面，最多 30 秒…',
                  style: TextStyle(fontSize: 12, color: t.fgMuted),
                ),
              ],
              if (_errorText != null) ...[
                const SizedBox(height: 10),
                Text(
                  _errorText!,
                  style: const TextStyle(
                    fontSize: 12.5,
                    height: 1.4,
                    color: AppTokens.danger500,
                  ),
                ),
                if (_phase == _Phase.failed) ...[
                  const SizedBox(height: 6),
                  OutlinedButton.icon(
                    key: const Key('offline-retry-analyze'),
                    icon: const Icon(Icons.refresh, size: 18),
                    label: const Text('重试分析'),
                    onPressed: _canAnalyze ? _analyze : null,
                  ),
                ],
              ],
            ],
          ),
        ),
        if (_phase == _Phase.ready || _phase == _Phase.submitting)
          ..._buildReadySection(),
      ],
    );
  }

  List<Widget> _buildReadySection() {
    final t = context.tokens;
    final items = _items;
    if (items.isEmpty) {
      return [
        const SizedBox(height: 12),
        const EmptyState(
          icon: Icons.playlist_remove_outlined,
          title: '没有可下载的项',
          subtitle: '换一个地址重新分析',
        ),
      ];
    }
    final estimated = items.any((item) => item.isEstimated);
    final summary =
        '已选 ${_selected.length} 项 · '
        '${estimated ? '约 ' : ''}${formatSize(_selectedBytes)}';
    return [
      const SizedBox(height: 12),
      AppCard(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    '清单',
                    style: TextStyle(
                      fontSize: 14,
                      fontWeight: FontWeight.w600,
                      color: t.fgDefault,
                    ),
                  ),
                ),
                Text(
                  '${items.length} 项',
                  style: TextStyle(fontSize: 12, color: t.fgMuted),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 4,
              children: [
                ActionChip(label: const Text('全选'), onPressed: _selectAll),
                ActionChip(
                  label: const Text('只要音频'),
                  onPressed: () => _selectByKind('audio'),
                ),
                ActionChip(
                  label: const Text('只要图片'),
                  onPressed: () => _selectByKind('image'),
                ),
                ActionChip(label: const Text('最高清'), onPressed: _selectHighest),
              ],
            ),
            const SizedBox(height: 4),
            ...items.map(
              (item) => Material(
                color: Colors.transparent,
                child: CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  dense: true,
                  value: _selected.contains(item.id),
                  onChanged: _phase == _Phase.submitting
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
                  title: Text(
                    item.title,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontSize: 13),
                  ),
                  subtitle: Text(
                    [
                      if (item.resolutionText.isNotEmpty) item.resolutionText,
                      if (item.durationMs > 0) formatDuration(item.durationMs),
                      if (item.estSize > 0)
                        '${item.isEstimated ? '约 ' : ''}${formatSize(item.estSize)}',
                    ].join(' · '),
                    style: TextStyle(fontSize: 11.5, color: t.fgMuted),
                  ),
                ),
              ),
            ),
            const Divider(height: 20),
            Text(summary, style: TextStyle(fontSize: 12.5, color: t.fgDefault)),
            const SizedBox(height: 10),
            Row(
              children: [
                Expanded(
                  child: Text(
                    '目标：${_targetLabel()}',
                    style: TextStyle(fontSize: 12, color: t.fgMuted),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                OutlinedButton.icon(
                  icon: const Icon(Icons.folder_open_outlined, size: 16),
                  label: const Text('选择'),
                  onPressed: _phase == _Phase.submitting ? null : _pickTarget,
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
                onChanged: _phase == _Phase.submitting
                    ? null
                    : (v) => setState(() => _packZip = v),
              ),
            ),
            const SizedBox(height: 4),
            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                icon: const Icon(Icons.download, size: 18),
                label: Text(_phase == _Phase.submitting ? '提交中…' : '开始下载'),
                onPressed: _phase == _Phase.submitting ? null : _submit,
              ),
            ),
          ],
        ),
      ),
    ];
  }

  String _targetLabel() {
    var diskName = '';
    for (final disk in _meta.writableDisks) {
      if (disk.diskId == _targetDiskId) {
        diskName = disk.name;
        break;
      }
    }
    if (diskName.isEmpty) return '未选择';
    final path = _targetPath.isEmpty ? '磁盘根目录' : _targetPath;
    return '$diskName / $path';
  }
}

/// 提示卡（不可用原因 + 可选操作按钮）
class _NoticeCard extends StatelessWidget {
  const _NoticeCard({required this.text, this.actionLabel, this.onAction});

  final String text;
  final String? actionLabel;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppTokens.warning500.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(t.rMd),
        border: Border.all(color: AppTokens.warning500.withValues(alpha: 0.35)),
      ),
      child: Row(
        children: [
          const Icon(
            Icons.info_outline_rounded,
            size: 18,
            color: AppTokens.warning500,
          ),
          SizedBox(width: t.s2),
          Expanded(
            child: Text(
              text,
              style: const TextStyle(fontSize: 12.5, height: 1.4),
            ),
          ),
          if (actionLabel != null && onAction != null)
            TextButton(onPressed: onAction, child: Text(actionLabel!)),
        ],
      ),
    );
  }
}
