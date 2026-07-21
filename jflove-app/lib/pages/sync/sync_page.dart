import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/sync_config.dart';
import '../../providers/session_provider.dart';
import '../../providers/sync_provider.dart';
import '../../services/disk_service.dart';
import '../../services/sync_engine_service.dart';
import '../../services/sync_service.dart';
import '../../utils/http_service.dart';
import '../../widgets/empty_state.dart';
import '../../widgets/loading_indicator.dart';

/// 同步管理页
///
/// 对标桌面端 sync_page.py。
class SyncPage extends ConsumerWidget {
  const SyncPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
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
              padding: const EdgeInsets.all(12),
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
        loading: () => const LoadingIndicator(),
        error: (e, _) => Center(child: Text('加载失败: $e')),
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

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // ── 第一行：图标 + 名称 + 操作按钮 ──
            Row(
              children: [
                Icon(
                  Icons.sync,
                  size: 20,
                  color: widget.config.enabled ? Colors.green : Colors.grey,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    widget.config.name,
                    style: theme.textTheme.titleSmall,
                  ),
                ),
                // 立即同步按钮
                if (widget.config.enabled)
                  Padding(
                    padding: const EdgeInsets.only(right: 4),
                    child: _isSyncing
                        ? SizedBox(
                            width: 20,
                            height: 20,
                            child: CircularProgressIndicator(
                              strokeWidth: 2,
                              color: theme.colorScheme.primary,
                            ),
                          )
                        : IconButton(
                            icon: Icon(
                              Icons.sync,
                              size: 20,
                              color: theme.colorScheme.primary,
                            ),
                            tooltip: '立即同步',
                            onPressed: widget.onSyncNow,
                            visualDensity: VisualDensity.compact,
                          ),
                  ),
                IconButton(
                  icon: const Icon(Icons.edit, size: 18),
                  onPressed: widget.onEdit,
                  visualDensity: VisualDensity.compact,
                ),
                IconButton(
                  icon: const Icon(Icons.delete, size: 18, color: Colors.red),
                  onPressed: widget.onDelete,
                  visualDensity: VisualDensity.compact,
                ),
              ],
            ),
            const SizedBox(height: 4),

            // ── 本地/远端路径 ──
            Text(
              '本地: ${widget.config.localPath}',
              style: theme.textTheme.bodySmall,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
            Text(
              '远端: 磁盘#${widget.config.diskId}/${widget.config.remotePath.isEmpty ? '(根目录)' : widget.config.remotePath}',
              style: theme.textTheme.bodySmall,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
            const SizedBox(height: 6),

            // ── 状态行：同步状态 + 标签 + 启用状态 ──
            Row(
              children: [
                // 同步状态文字
                if (_statusText.isNotEmpty)
                  Expanded(
                    child: Text(
                      _statusText,
                      style: TextStyle(
                        fontSize: 12,
                        color: _statusText.startsWith('失败')
                            ? Colors.red
                            : _statusText.startsWith('完成')
                            ? Colors.green
                            : theme.colorScheme.primary,
                      ),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                if (_statusText.isNotEmpty) const SizedBox(width: 8),

                // 同步模式标签
                Chip(
                  label: Text(
                    widget.config.autoSync && widget.config.enabled
                        ? '自动同步 ${widget.config.syncInterval}s'
                        : '手动同步',
                    style: const TextStyle(fontSize: 11),
                  ),
                  visualDensity: VisualDensity.compact,
                  padding: EdgeInsets.zero,
                  materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                ),
                const SizedBox(width: 8),

                // 启用/禁用
                Text(
                  widget.config.enabled ? '已启用' : '已禁用',
                  style: TextStyle(
                    fontSize: 12,
                    color: widget.config.enabled ? Colors.green : Colors.grey,
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

// ============ 同步配置编辑对话框 ============

/// 同步配置编辑对话框
///
/// 使用下拉选择器 + 目录浏览器替代手动输入，
/// 对标 settings_page.dart 中的 _NotesDirDialog。
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
    setState(() => _loadingDirs = true);
    try {
      final dirs = await widget.diskService.browseDirs(
        _selectedDiskId!,
        path: _pathStack.last,
      );
      if (mounted) {
        setState(() {
          _subdirs = dirs;
          _loadingDirs = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _subdirs = [];
          _loadingDirs = false;
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
    final theme = Theme.of(context);
    final isEditing = widget.existing != null;

    return AlertDialog(
      title: Text(isEditing ? '编辑同步配置' : '新建同步配置'),
      content: SizedBox(
        width: 400,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // ── 配置名称 ──
              TextField(
                controller: _nameCtrl,
                decoration: const InputDecoration(
                  labelText: '配置名称',
                  hintText: '如：照片备份',
                  prefixIcon: Icon(Icons.label_outline, size: 20),
                ),
              ),
              const SizedBox(height: 12),

              // ── 本地目录 ──
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
              const SizedBox(height: 16),

              // ── 远端磁盘选择 ──
              const Text(
                '远端磁盘',
                style: TextStyle(fontSize: 13, fontWeight: FontWeight.w500),
              ),
              const SizedBox(height: 4),
              _loadingDisks
                  ? const SizedBox(
                      height: 40,
                      child: Center(child: LinearProgressIndicator()),
                    )
                  : _diskError != null
                  ? Text(
                      '加载失败: $_diskError',
                      style: const TextStyle(color: Colors.red, fontSize: 12),
                    )
                  : InputDecorator(
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
              const SizedBox(height: 12),

              // ── 远端目录浏览 ──
              const Text(
                '远端目录（可选）',
                style: TextStyle(fontSize: 13, fontWeight: FontWeight.w500),
              ),
              const SizedBox(height: 4),
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
              const SizedBox(height: 4),
              _selectedDiskId == null
                  ? const Padding(
                      padding: EdgeInsets.all(16),
                      child: Center(
                        child: Text(
                          '请先选择远端磁盘',
                          style: TextStyle(color: Colors.grey, fontSize: 12),
                        ),
                      ),
                    )
                  : _loadingDirs
                  ? const Padding(
                      padding: EdgeInsets.all(16),
                      child: Center(
                        child: CircularProgressIndicator(strokeWidth: 2),
                      ),
                    )
                  : _subdirs.isEmpty
                  ? const Padding(
                      padding: EdgeInsets.all(16),
                      child: Center(
                        child: Text(
                          '（此目录下没有子文件夹）',
                          style: TextStyle(color: Colors.grey, fontSize: 12),
                        ),
                      ),
                    )
                  : SizedBox(
                      height: 160,
                      child: ListView.builder(
                        itemCount: _subdirs.length,
                        itemBuilder: (ctx, i) {
                          final dir = _subdirs[i];
                          return ListTile(
                            dense: true,
                            leading: const Icon(
                              Icons.folder_outlined,
                              size: 20,
                            ),
                            title: Text(
                              dir['name'] as String,
                              style: const TextStyle(fontSize: 14),
                            ),
                            onTap: () => _enterDir(dir['path'] as String),
                          );
                        },
                      ),
                    ),
              const SizedBox(height: 8),

              // ── 自动同步 ──
              Row(
                children: [
                  const Text('自动同步'),
                  const Spacer(),
                  Switch(
                    value: _autoSync,
                    onChanged: (v) => setState(() => _autoSync = v),
                  ),
                ],
              ),
              if (_autoSync) ...[
                const SizedBox(height: 8),
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
}

// ============ 本地目录浏览对话框 ============

/// 本地目录浏览对话框
///
/// 让用户浏览设备文件系统并选择目录，
/// 从常用起始路径（如 /storage/emulated/0）开始导航。
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
    final theme = Theme.of(context);
    final currentDir = _pathStack.last;
    final dirName = currentDir.split('/').last;

    return AlertDialog(
      title: Text('选择目录: $dirName'),
      content: SizedBox(
        width: 360,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
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
            const SizedBox(height: 8),
            _loading
                ? const Padding(
                    padding: EdgeInsets.all(24),
                    child: Center(child: CircularProgressIndicator()),
                  )
                : _errorMsg != null
                ? Padding(
                    padding: const EdgeInsets.all(16),
                    child: Text(
                      '读取失败: $_errorMsg',
                      style: const TextStyle(color: Colors.red),
                    ),
                  )
                : _entries.isEmpty
                ? const Padding(
                    padding: EdgeInsets.all(24),
                    child: Center(
                      child: Text(
                        '（此目录下没有子文件夹）',
                        style: TextStyle(color: Colors.grey),
                      ),
                    ),
                  )
                : SizedBox(
                    height: 240,
                    child: ListView.builder(
                      itemCount: _entries.length,
                      itemBuilder: (ctx, i) {
                        final entry = _entries[i];
                        final name = entry.path.split('/').last;
                        return ListTile(
                          dense: true,
                          leading: const Icon(Icons.folder_outlined, size: 20),
                          title: Text(name),
                          trailing: const Icon(Icons.chevron_right, size: 16),
                          onTap: () => _enterDir(entry.path),
                        );
                      },
                    ),
                  ),
          ],
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
