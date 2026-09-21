import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../config/design_tokens.dart';
import '../../models/sync_config.dart';
import '../../providers/session_provider.dart';
import '../../providers/sync_provider.dart';
import '../../services/disk_service.dart';
import '../../services/sync_engine_service.dart';
import '../../services/sync_service.dart';
import '../../utils/http_service.dart';
import '../../widgets/app_card.dart';
import '../../widgets/empty_state.dart';
import '../../widgets/error_state.dart';

/// 同步管理页（v1.5.0：视觉层令牌化改造）
///
/// 对标桌面端 sync_page.py。
///
/// 本次改造**只动视觉层**，业务逻辑（provider 调用、同步引擎交互、导航、
/// 表单校验）与 v1.4.2 完全一致：
///   1. 硬编码颜色常量（Material 调色板与十六进制字面量）全部换成
///      `context.tokens` / `AppTokens` 语义色；
///   2. 配置卡片与对话框表单区块统一改用 `AppCard`（描边 + 极浅阴影，
///      消除 Material 默认 elevation 浮起）；
///   3. 状态标记（同步中/完成/失败/同步模式/启用状态）用 `AppBadge`
///      取代原先手写的彩色文字与裸 `Chip`；
///   4. 空 / 加载 / 失败三态分别改用 `EmptyState` / `ListSkeleton`（或
///      `SkeletonBox`）/ `ErrorState`；
///   5. 圆角、间距、阴影统一取 `t.rMd` / `t.rLg` / `t.s1`~`t.s6` / `t.e1`。
///
/// v1.5.0 修补：删除「配置总数 / 已启用 / 自动同步」三张 `StatCard` 概览
/// （用户反馈"文档管理上面一堆小卡片都不需要"），页面其余布局与功能不变。
/// 列表本身就是概览，不再单独占一行统计卡。
class SyncPage extends ConsumerWidget {
  const SyncPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.tokens;
    final configAsync = ref.watch(syncConfigListProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('同步管理'),
        actions: [
          IconButton(
            icon: const Icon(Icons.add),
            tooltip: '新建配置',
            onPressed: () => _showCreateDialog(context, ref),
          ),
        ],
      ),
      body: configAsync.when(
        data: (configs) {
          // 确保同步引擎加载配置列表，否则 triggerSync 找不到 config 会静默失败
          ref.read(syncEngineServiceProvider).reloadConfigs(configs);

          if (configs.isEmpty) {
            return const EmptyState(
              icon: Icons.sync,
              title: '暂无同步配置',
              subtitle: '添加同步配置来备份文件',
            );
          }
          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(syncConfigListProvider),
            child: ListView.builder(
              padding: EdgeInsets.fromLTRB(t.s3, t.s3, t.s3, t.s6),
              itemCount: configs.length,
              itemBuilder: (ctx, i) => _SyncConfigCard(
                config: configs[i],
                onEdit: () => _showEditDialog(context, ref, configs[i]),
                onDelete: () => _deleteConfig(context, ref, configs[i]),
                onSyncNow: () {
                  ref
                      .read(syncEngineServiceProvider)
                      .triggerSync(configs[i].id);
                },
              ),
            ),
          );
        },
        loading: () => const ListSkeleton(),
        error: (e, _) => ErrorState(
          title: '同步配置加载失败',
          message: '$e',
          onRetry: () => ref.invalidate(syncConfigListProvider),
        ),
      ),
    );
  }

  Future<void> _showCreateDialog(BuildContext context, WidgetRef ref) async {
    final session = ref.read(sessionManagerProvider);
    final http = HttpService(session);
    final diskService = DiskService(http);
    final syncService = SyncService(http, session);

    final result = await showDialog<_SyncConfigResult>(
      context: context,
      builder: (ctx) =>
          _SyncConfigDialog(diskService: diskService, syncService: syncService),
    );
    if (result != null && context.mounted) {
      _saveConfig(context, ref, null, result);
    }
  }

  Future<void> _showEditDialog(
    BuildContext context,
    WidgetRef ref,
    SyncConfig config,
  ) async {
    final session = ref.read(sessionManagerProvider);
    final http = HttpService(session);
    final diskService = DiskService(http);
    final syncService = SyncService(http, session);

    final result = await showDialog<_SyncConfigResult>(
      context: context,
      builder: (ctx) => _SyncConfigDialog(
        existing: config,
        diskService: diskService,
        syncService: syncService,
      ),
    );
    if (result != null && context.mounted) {
      _saveConfig(context, ref, config, result);
    }
  }

  Future<void> _saveConfig(
    BuildContext context,
    WidgetRef ref,
    SyncConfig? existing,
    _SyncConfigResult result,
  ) async {
    try {
      if (existing != null) {
        final updated = existing.copyWith(
          name: result.name,
          localPath: result.localPath,
          diskId: result.diskId,
          remotePath: result.remotePath,
          autoSync: result.autoSync,
          syncInterval: result.syncInterval,
          enabled: result.enabled,
        );
        final configs = await ref.read(syncServiceProvider).loadConfigs();
        final index = configs.indexWhere((c) => c.id == existing.id);
        if (index >= 0) {
          configs[index] = updated;
        }
        await ref.read(syncServiceProvider).saveConfigs(configs);
      } else {
        final newConfig = SyncConfig(
          id: DateTime.now().millisecondsSinceEpoch.toString(),
          name: result.name,
          diskId: result.diskId,
          remotePath: result.remotePath,
          localPath: result.localPath,
          autoSync: result.autoSync,
          syncInterval: result.syncInterval,
          enabled: result.enabled,
        );
        final configs = await ref.read(syncServiceProvider).loadConfigs();
        configs.add(newConfig);
        await ref.read(syncServiceProvider).saveConfigs(configs);
      }
      ref.invalidate(syncConfigListProvider);
      if (context.mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('同步配置已保存')));
      }
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('保存失败: $e')));
      }
    }
  }

  Future<void> _deleteConfig(
    BuildContext context,
    WidgetRef ref,
    SyncConfig config,
  ) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('删除同步配置'),
        content: Text('确定要删除「${config.name}」吗？\n仅删除映射关系，不影响文件。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('取消'),
          ),
          FilledButton(
            // 危险操作用语义色 danger-500，文字色取主题 onPrimary（不自造色值）
            style: FilledButton.styleFrom(
              backgroundColor: AppTokens.danger500,
              foregroundColor: Theme.of(ctx).colorScheme.onPrimary,
            ),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('删除'),
          ),
        ],
      ),
    );

    if (confirmed == true) {
      try {
        final configs = await ref.read(syncServiceProvider).loadConfigs();
        configs.removeWhere((c) => c.id == config.id);
        await ref.read(syncServiceProvider).saveConfigs(configs);
        ref.invalidate(syncConfigListProvider);
      } catch (e) {
        if (context.mounted) {
          ScaffoldMessenger.of(
            context,
          ).showSnackBar(SnackBar(content: Text('删除失败: $e')));
        }
      }
    }
  }
}

/// 同步配置卡片
///
/// 显示同步配置详情，提供"立即同步"、编辑、删除操作按钮，并实时反映同步状态。
class _SyncConfigCard extends ConsumerStatefulWidget {
  final SyncConfig config;
  final VoidCallback onEdit;
  final VoidCallback onDelete;
  final VoidCallback onSyncNow;

  const _SyncConfigCard({
    required this.config,
    required this.onEdit,
    required this.onDelete,
    required this.onSyncNow,
  });

  @override
  ConsumerState<_SyncConfigCard> createState() => _SyncConfigCardState();
}

class _SyncConfigCardState extends ConsumerState<_SyncConfigCard> {
  String _statusText = '';
  bool _isSyncing = false;
  StreamSubscription<SyncEvent>? _syncSub;

  @override
  void initState() {
    super.initState();
    _listenToSyncEvents();
  }

  void _listenToSyncEvents() {
    final engine = ref.read(syncEngineServiceProvider);
    _syncSub = engine.eventStream.listen((event) {
      if (!mounted) return;
      if (event is SyncStarted && event.configId == widget.config.id) {
        setState(() {
          _statusText = '正在同步…';
          _isSyncing = true;
        });
      } else if (event is SyncProgress && event.configId == widget.config.id) {
        setState(() {
          _statusText =
              '进行中 ↑${event.uploaded} ↓${event.downloaded} ⏭${event.skipped}';
        });
      } else if (event is SyncFinished && event.configId == widget.config.id) {
        setState(() {
          _statusText =
              '完成 ↑${event.uploaded} ↓${event.downloaded} ⏭${event.skipped}';
          _isSyncing = false;
        });
        // 3 秒后清除状态文字
        Future.delayed(const Duration(seconds: 3), () {
          if (mounted) setState(() => _statusText = '');
        });
      } else if (event is SyncError && event.configId == widget.config.id) {
        setState(() {
          _statusText = '失败: ${event.error}';
          _isSyncing = false;
        });
        Future.delayed(const Duration(seconds: 5), () {
          if (mounted) setState(() => _statusText = '');
        });
      }
    });
  }

  @override
  void dispose() {
    _syncSub?.cancel();
    super.dispose();
  }

  /// 同步状态徽标色调：失败→危险，完成→成功，其余（同步中/进行中）→品牌
  BadgeTone get _statusTone {
    if (_statusText.startsWith('失败')) return BadgeTone.danger;
    if (_statusText.startsWith('完成')) return BadgeTone.success;
    return BadgeTone.brand;
  }

  /// 同步状态徽标图标
  IconData get _statusIcon {
    if (_statusText.startsWith('失败')) return Icons.error_outline;
    if (_statusText.startsWith('完成')) return Icons.check_circle_outline;
    return Icons.sync;
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    final theme = Theme.of(context);
    final config = widget.config;
    final isAuto = config.autoSync && config.enabled;

    return AppCard(
      margin: EdgeInsets.only(bottom: t.s3),
      padding: EdgeInsets.all(t.s3),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // ── 第一行：图标 + 名称 + 操作按钮 ──
          Row(
            children: [
              Icon(
                Icons.sync,
                size: 20,
                color: config.enabled ? AppTokens.success500 : t.fgSubtle,
              ),
              SizedBox(width: t.s2),
              Expanded(
                child: Text(
                  config.name,
                  style: theme.textTheme.titleSmall,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              // 立即同步按钮
              if (config.enabled)
                Padding(
                  padding: EdgeInsets.only(right: t.s1),
                  child: _isSyncing
                      ? const SizedBox(
                          width: 20,
                          height: 20,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: AppTokens.brand500,
                          ),
                        )
                      : IconButton(
                          icon: const Icon(
                            Icons.sync,
                            size: 20,
                            color: AppTokens.brand600,
                          ),
                          tooltip: '立即同步',
                          onPressed: widget.onSyncNow,
                          visualDensity: VisualDensity.compact,
                        ),
                ),
              IconButton(
                icon: Icon(Icons.edit, size: 18, color: t.fgMuted),
                tooltip: '编辑配置',
                onPressed: widget.onEdit,
                visualDensity: VisualDensity.compact,
              ),
              IconButton(
                icon: const Icon(
                  Icons.delete,
                  size: 18,
                  color: AppTokens.danger500,
                ),
                tooltip: '删除配置',
                onPressed: widget.onDelete,
                visualDensity: VisualDensity.compact,
              ),
            ],
          ),
          SizedBox(height: t.s2),

          // ── 本地/远端路径 ──
          Text(
            '本地: ${config.localPath}',
            style: theme.textTheme.bodySmall,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          Text(
            '远端: 磁盘#${config.diskId}/${config.remotePath.isEmpty ? '(根目录)' : config.remotePath}',
            style: theme.textTheme.bodySmall,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          SizedBox(height: t.s3),

          // ── 状态行：同步状态 + 同步模式 + 启用状态（统一用 AppBadge）──
          Wrap(
            spacing: t.s2,
            runSpacing: t.s1,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              // 同步进度/结果标记（原先是手写彩色文字）
              if (_statusText.isNotEmpty)
                AppBadge(_statusText, tone: _statusTone, icon: _statusIcon),
              // 同步模式标记（原先是裸 Chip）
              AppBadge(
                isAuto ? '自动同步 ${config.syncInterval}s' : '手动同步',
                tone: isAuto ? BadgeTone.brand : BadgeTone.neutral,
                icon: isAuto ? Icons.timer_outlined : Icons.touch_app,
              ),
              // 启用状态标记（原先是手写彩色文字）
              AppBadge(
                config.enabled ? '已启用' : '已禁用',
                tone: config.enabled ? BadgeTone.success : BadgeTone.neutral,
                icon: config.enabled
                    ? Icons.check_circle_outline
                    : Icons.pause_circle_outline,
              ),
            ],
          ),
        ],
      ),
    );
  }
}

// ============ 同步配置编辑对话框 ============

/// 同步配置编辑对话框
///
/// 使用下拉选择器 + 目录浏览器替代手动输入，
/// 对标 settings_page.dart 中的 _NotesDirDialog。
/// v1.5.0：表单区块统一包进 `AppCard`，颜色/圆角/间距全部走设计令牌。
class _SyncConfigDialog extends StatefulWidget {
  final SyncConfig? existing;
  final DiskService diskService;
  final SyncService syncService;

  const _SyncConfigDialog({
    this.existing,
    required this.diskService,
    required this.syncService,
  });

  @override
  State<_SyncConfigDialog> createState() => _SyncConfigDialogState();
}

/// 对话框返回结果
class _SyncConfigResult {
  final String name;
  final String localPath;
  final int diskId;
  final String remotePath;
  final bool autoSync;
  final int syncInterval;
  final bool enabled;

  const _SyncConfigResult({
    required this.name,
    required this.localPath,
    required this.diskId,
    required this.remotePath,
    required this.autoSync,
    required this.syncInterval,
    required this.enabled,
  });
}

class _SyncConfigDialogState extends State<_SyncConfigDialog> {
  late TextEditingController _nameCtrl;
  late TextEditingController _localCtrl;
  late TextEditingController _intervalCtrl;
  late int? _selectedDiskId;
  late bool _autoSync;
  late int _interval;
  late bool _enabled;

  // 远端目录浏览状态
  List<Map<String, dynamic>> _disks = [];
  bool _loadingDisks = true;
  List<Map<String, dynamic>> _subdirs = [];
  final List<String> _pathStack = [''];
  bool _loadingDirs = false;
  String? _diskError;

  /// 远端子目录列表的**读取失败**信息。
  ///
  /// 为什么必须有这个字段（v1.5.0 反馈修复）：原先 `_loadSubdirs` 的 catch 只是把
  /// `_subdirs` 清空，界面因此落到「此目录下没有子文件夹」的空态 ——
  /// **网络失败会被显示成"这个目录是空的"**，用户据此做出错误判断（以为远端没内容），
  /// 而且没有任何重试入口。属于典型的"静默失败"，必须显式区分
  /// 「加载中 / 读取失败（可重试）/ 确实是空」三态（本地目录选择器早已是三态）。
  String? _subdirsError;

  @override
  void initState() {
    super.initState();
    final e = widget.existing;
    _nameCtrl = TextEditingController(text: e?.name ?? '');
    _localCtrl = TextEditingController(text: e?.localPath ?? '');
    _intervalCtrl = TextEditingController(
      text: (e?.syncInterval ?? 300).toString(),
    );
    _selectedDiskId = e?.diskId;
    _autoSync = e?.autoSync ?? false;
    _interval = e?.syncInterval ?? 300;
    _enabled = e?.enabled ?? true;
    // 初始化路径栈（如果已有远端路径，从该路径开始）
    final remotePath = e?.remotePath ?? '';
    if (remotePath.isNotEmpty) {
      _pathStack.clear();
      _pathStack.add(remotePath);
    }
    _loadDisks();
  }

  @override
  void dispose() {
    _nameCtrl.dispose();
    _localCtrl.dispose();
    _intervalCtrl.dispose();
    super.dispose();
  }

  Future<void> _loadDisks() async {
    setState(() {
      _loadingDisks = true;
      _diskError = null;
    });
    try {
      final disks = await widget.diskService.listAccessibleDisks();
      if (mounted) {
        setState(() {
          _disks = disks.map((d) => {'id': d.id, 'name': d.name}).toList();
          _loadingDisks = false;
        });
        if (_selectedDiskId != null &&
            _disks.any((d) => d['id'] == _selectedDiskId)) {
          _loadSubdirs();
        }
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _diskError = e.toString();
          _loadingDisks = false;
        });
      }
    }
  }

  Future<void> _loadSubdirs() async {
    if (_selectedDiskId == null) return;
    setState(() {
      _loadingDirs = true;
      // 重试时先清掉上一次的错误，否则错误态会一直盖住新的加载中/成功态
      _subdirsError = null;
    });
    try {
      final dirs = await widget.diskService.browseDirs(
        _selectedDiskId!,
        path: _pathStack.last,
      );
      if (mounted) {
        setState(() {
          _subdirs = dirs;
          _loadingDirs = false;
          _subdirsError = null;
        });
      }
    } catch (e) {
      if (mounted) {
        // 不能只清空列表：那会让"读取失败"看起来像"目录是空的"，用户无法察觉。
        setState(() {
          _subdirs = [];
          _loadingDirs = false;
          _subdirsError = e.toString();
        });
      }
    }
  }

  void _onDiskChanged(int? diskId) {
    setState(() {
      _selectedDiskId = diskId;
      _pathStack
        ..clear()
        ..add('');
    });
    if (diskId != null) _loadSubdirs();
  }

  void _enterDir(String dirPath) {
    setState(() => _pathStack.add(dirPath));
    _loadSubdirs();
  }

  void _goBack() {
    if (_pathStack.length > 1) {
      setState(() => _pathStack.removeLast());
      _loadSubdirs();
    }
  }

  void _goRoot() {
    setState(() {
      _pathStack
        ..clear()
        ..add('');
    });
    _loadSubdirs();
  }

  String get _currentRemotePath => _pathStack.last;

  Future<void> _save() async {
    final name = _nameCtrl.text.trim();
    if (name.isEmpty) {
      _showSnack('请输入配置名称');
      return;
    }
    if (_selectedDiskId == null) {
      _showSnack('请选择远端磁盘');
      return;
    }

    Navigator.pop(
      context,
      _SyncConfigResult(
        name: name,
        localPath: _localCtrl.text.trim(),
        diskId: _selectedDiskId!,
        remotePath: _currentRemotePath,
        autoSync: _autoSync,
        syncInterval: _interval,
        enabled: _enabled,
      ),
    );
  }

  void _showSnack(String msg) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(msg), duration: const Duration(seconds: 2)),
    );
  }

  void _openLocalDirBrowser() {
    showDialog<String>(
      context: context,
      builder: (ctx) => _LocalDirBrowserDialog(
        initialPath: _localCtrl.text,
        onSelected: (path) {
          _localCtrl.text = path;
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    final theme = Theme.of(context);
    final isEditing = widget.existing != null;

    return AlertDialog(
      title: Text(isEditing ? '编辑同步配置' : '新建同步配置'),
      content: SizedBox(
        width: 400,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              // ── 第一区块：配置名称 + 本地目录 ──
              AppCard(
                padding: EdgeInsets.all(t.s3),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    TextField(
                      controller: _nameCtrl,
                      decoration: const InputDecoration(
                        labelText: '配置名称',
                        hintText: '如：照片备份',
                        prefixIcon: Icon(Icons.label_outline, size: 20),
                      ),
                    ),
                    SizedBox(height: t.s3),
                    TextField(
                      controller: _localCtrl,
                      decoration: InputDecoration(
                        labelText: '本地目录',
                        hintText: '/storage/emulated/0/DCIM',
                        prefixIcon: const Icon(Icons.folder_outlined, size: 20),
                        suffixIcon: IconButton(
                          icon: const Icon(Icons.folder_open, size: 20),
                          tooltip: '浏览本地目录',
                          onPressed: _openLocalDirBrowser,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              SizedBox(height: t.s2),

              // ── 第二区块：远端磁盘 + 远端目录浏览 ──
              AppCard(
                padding: EdgeInsets.all(t.s3),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const SectionTitle('远端存储'),
                    SizedBox(height: t.s3),
                    const _FieldLabel('远端磁盘'),
                    SizedBox(height: t.s1),
                    // 磁盘列表三态：加载中→骨架屏；失败→错误态（可重试）；成功→下拉框
                    if (_loadingDisks)
                      SizedBox(
                        width: double.infinity,
                        child: SkeletonBox(height: 44, radius: t.rMd),
                      )
                    else if (_diskError != null)
                      ErrorState(
                        title: '磁盘列表加载失败',
                        message: '$_diskError',
                        onRetry: _loadDisks,
                      )
                    else
                      InputDecorator(
                        decoration: const InputDecoration(
                          isDense: true,
                          contentPadding: EdgeInsets.symmetric(
                            horizontal: 12,
                            vertical: 8,
                          ),
                        ),
                        child: DropdownButton<int>(
                          value: _disks.any((d) => d['id'] == _selectedDiskId)
                              ? _selectedDiskId
                              : null,
                          isDense: true,
                          isExpanded: true,
                          underline: const SizedBox(),
                          hint: const Text('选择虚拟磁盘'),
                          items: _disks.map((d) {
                            final id = d['id'] as int;
                            return DropdownMenuItem<int>(
                              value: id,
                              child: Text(d['name'] as String),
                            );
                          }).toList(),
                          onChanged: _onDiskChanged,
                        ),
                      ),
                    SizedBox(height: t.s3),

                    // ── 远端目录导航 ──
                    const _FieldLabel('远端目录（可选）'),
                    SizedBox(height: t.s1),
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            '路径: ${_currentRemotePath.isEmpty ? "（根目录）" : "/$_currentRemotePath"}',
                            style: theme.textTheme.bodySmall,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                        if (_pathStack.length > 1)
                          IconButton(
                            icon: const Icon(Icons.arrow_upward, size: 18),
                            tooltip: '返回上级',
                            onPressed: _goBack,
                            visualDensity: VisualDensity.compact,
                          ),
                        IconButton(
                          icon: const Icon(Icons.home, size: 18),
                          tooltip: '根目录',
                          onPressed: _goRoot,
                          visualDensity: VisualDensity.compact,
                        ),
                      ],
                    ),
                    SizedBox(height: t.s1),
                    _buildRemoteDirArea(t),
                  ],
                ),
              ),
              SizedBox(height: t.s2),

              // ── 第三区块：自动同步 ──
              AppCard(
                padding: EdgeInsets.all(t.s3),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            '自动同步',
                            style: TextStyle(
                              fontSize: 13.5,
                              fontWeight: FontWeight.w500,
                              color: t.fgDefault,
                            ),
                          ),
                        ),
                        Switch(
                          value: _autoSync,
                          onChanged: (v) => setState(() => _autoSync = v),
                        ),
                      ],
                    ),
                    if (_autoSync) ...[
                      SizedBox(height: t.s2),
                      TextField(
                        controller: _intervalCtrl,
                        decoration: const InputDecoration(
                          labelText: '同步间隔（秒）',
                          hintText: '300',
                          prefixIcon: Icon(Icons.timer_outlined, size: 20),
                        ),
                        keyboardType: TextInputType.number,
                        onChanged: (v) => _interval = int.tryParse(v) ?? 300,
                      ),
                    ],
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('取消'),
        ),
        FilledButton(onPressed: _save, child: const Text('保存')),
      ],
    );
  }

  /// 远端目录浏览区
  ///
  /// 用下沉面（`bgSunken` + 描边 + `rMd`）而不是再套一层 `AppCard`，
  /// 避免"卡片套卡片"；四态：未选磁盘 / 加载中 / 空 / 有子目录。
  Widget _buildRemoteDirArea(AppTokens t) {
    return Container(
      height: 180,
      decoration: BoxDecoration(
        color: t.bgSunken,
        borderRadius: BorderRadius.circular(t.rMd),
        border: Border.all(color: t.borderSubtle),
      ),
      clipBehavior: Clip.antiAlias,
      child: _selectedDiskId == null
          ? Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(Icons.storage, size: 22, color: t.fgSubtle),
                  SizedBox(height: t.s2),
                  Text(
                    '请先选择远端磁盘',
                    style: TextStyle(fontSize: 12.5, color: t.fgMuted),
                  ),
                ],
              ),
            )
          : _loadingDirs
          ? const ListSkeleton(rows: 2)
          : _subdirsError != null
          // 读取失败必须与"确实是空目录"区分开，否则网络失败会被误读成"远端没内容"
          ? ErrorState(
              title: '目录读取失败',
              message: '$_subdirsError',
              onRetry: _loadSubdirs,
            )
          : _subdirs.isEmpty
          ? const EmptyState(
              icon: Icons.folder_off_outlined,
              title: '此目录下没有子文件夹',
            )
          // ListTile 的水波纹画在最近的 Material 上；下沉面是 DecoratedBox，
          // 不套一层透明 Material 的话水波纹会被底色盖住（框架会报
          // "ListTile ... ink splashes may be invisible"）
          : Material(
              type: MaterialType.transparency,
              child: ListView.builder(
                padding: EdgeInsets.symmetric(vertical: t.s1),
                itemCount: _subdirs.length,
                itemBuilder: (ctx, i) {
                  final dir = _subdirs[i];
                  return ListTile(
                    dense: true,
                    leading: Icon(
                      Icons.folder_outlined,
                      size: 20,
                      color: t.fgMuted,
                    ),
                    title: Text(
                      dir['name'] as String,
                      style: TextStyle(fontSize: 14, color: t.fgDefault),
                    ),
                    onTap: () => _enterDir(dir['path'] as String),
                  );
                },
              ),
            ),
    );
  }
}

/// 表单区块内的小标签（12.5px / fgMuted）
class _FieldLabel extends StatelessWidget {
  final String text;

  const _FieldLabel(this.text);

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    return Text(
      text,
      style: TextStyle(
        fontSize: 12.5,
        fontWeight: FontWeight.w600,
        color: t.fgMuted,
      ),
    );
  }
}

// ============ 本地目录浏览对话框 ============

/// 本地目录浏览对话框
///
/// 让用户浏览设备文件系统并选择目录，
/// 从常用起始路径（如 /storage/emulated/0）开始导航。
/// v1.5.0：列表区改用 `AppCard`，三态改用 `ListSkeleton` / `ErrorState` / `EmptyState`。
class _LocalDirBrowserDialog extends StatefulWidget {
  final String initialPath;
  final ValueChanged<String> onSelected;

  const _LocalDirBrowserDialog({
    required this.initialPath,
    required this.onSelected,
  });

  @override
  State<_LocalDirBrowserDialog> createState() => _LocalDirBrowserDialogState();
}

class _LocalDirBrowserDialogState extends State<_LocalDirBrowserDialog> {
  /// 常用起始路径（Android 设备存储根目录）
  static const List<String> _commonRoots = ['/storage/emulated/0', '/storage'];

  late List<String> _pathStack;
  List<FileSystemEntity> _entries = [];
  bool _loading = false;
  String? _errorMsg;
  // 保存当前选中的目录路径（等用户点击"选择此目录"时使用）
  String _currentDirPath = '';

  @override
  void initState() {
    super.initState();
    // 如果初始路径非空且存在，从该路径开始
    final init = widget.initialPath;
    if (init.isNotEmpty && Directory(init).existsSync()) {
      _pathStack = [init];
    } else {
      // 找到第一个存在的常用根路径
      final firstExisting = _commonRoots.firstWhere(
        (p) => Directory(p).existsSync(),
        orElse: () => '/storage/emulated/0',
      );
      _pathStack = [firstExisting];
    }
    _currentDirPath = _pathStack.last;
    _loadDir();
  }

  Future<void> _loadDir() async {
    setState(() {
      _loading = true;
      _errorMsg = null;
    });
    try {
      final dir = Directory(_pathStack.last);
      final entities = dir.listSync(followLinks: false);
      // 只保留目录，按名称排序
      final dirs =
          entities
              .whereType<Directory>()
              .where((d) => !d.path.split('/').last.startsWith('.'))
              .toList()
            ..sort(
              (a, b) => a.path
                  .split('/')
                  .last
                  .toLowerCase()
                  .compareTo(b.path.split('/').last.toLowerCase()),
            );
      if (mounted) {
        setState(() {
          _entries = dirs;
          _loading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _errorMsg = e.toString();
          _loading = false;
        });
      }
    }
  }

  void _enterDir(String dirPath) {
    setState(() {
      _pathStack.add(dirPath);
      _currentDirPath = dirPath;
    });
    _loadDir();
  }

  void _goBack() {
    if (_pathStack.length > 1) {
      setState(() => _pathStack.removeLast());
      _currentDirPath = _pathStack.last;
      _loadDir();
    }
  }

  void _selectCurrentDir() {
    Navigator.pop(context, _currentDirPath);
    widget.onSelected(_currentDirPath);
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    final theme = Theme.of(context);
    final currentDir = _pathStack.last;
    final dirName = currentDir.split('/').last;

    return AlertDialog(
      title: Text('选择目录: $dirName'),
      content: SizedBox(
        width: 360,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      currentDir,
                      style: theme.textTheme.bodySmall,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  if (_pathStack.length > 1)
                    IconButton(
                      icon: const Icon(Icons.arrow_upward, size: 18),
                      tooltip: '返回上级',
                      onPressed: _goBack,
                      visualDensity: VisualDensity.compact,
                    ),
                ],
              ),
              SizedBox(height: t.s2),
              // ── 目录列表三态：加载中→骨架屏；读取失败→错误态（可重试）；空→空状态 ──
              if (_loading)
                SizedBox(height: 200, child: ListSkeleton(rows: 3))
              else if (_errorMsg != null)
                ErrorState(
                  title: '目录读取失败',
                  message: '$_errorMsg',
                  onRetry: _loadDir,
                )
              else if (_entries.isEmpty)
                const EmptyState(
                  icon: Icons.folder_off_outlined,
                  title: '此目录下没有子文件夹',
                )
              else
                AppCard(
                  padding: EdgeInsets.zero,
                  // 同上：ListTile 需要一层紧邻的 Material 才能正常画水波纹
                  child: Material(
                    type: MaterialType.transparency,
                    child: SizedBox(
                      height: 240,
                      child: ListView.builder(
                        padding: EdgeInsets.symmetric(vertical: t.s1),
                        itemCount: _entries.length,
                        itemBuilder: (ctx, i) {
                          final entry = _entries[i];
                          final name = entry.path.split('/').last;
                          return ListTile(
                            dense: true,
                            leading: Icon(
                              Icons.folder_outlined,
                              size: 20,
                              color: t.fgMuted,
                            ),
                            title: Text(
                              name,
                              style: TextStyle(
                                fontSize: 14,
                                color: t.fgDefault,
                              ),
                            ),
                            trailing: Icon(
                              Icons.chevron_right,
                              size: 16,
                              color: t.fgSubtle,
                            ),
                            onTap: () => _enterDir(entry.path),
                          );
                        },
                      ),
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('取消'),
        ),
        FilledButton.tonalIcon(
          icon: const Icon(Icons.check, size: 18),
          label: const Text('选择此目录'),
          onPressed: _selectCurrentDir,
        ),
      ],
    );
  }
}
