import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/design_tokens.dart';
import '../providers/file_provider.dart';
import '../widgets/empty_state.dart';

/// 目录选择器里的磁盘（屏蔽 VirtualDisk / OfflineWritableDisk 的字段差异）
class DirPickerDisk {
  const DirPickerDisk({required this.id, required this.name});

  final int id;
  final String name;
}

/// 虚拟磁盘目录选择器（v1.8.1 / D13、AC-257、AC-259）
///
/// 「磁盘下拉 + 磁盘内目录逐级浏览 + 选择当前目录」，供以下场景复用：
///   - 离线下载新建任务 / 默认目录；
///   - 相册备份的远端目标目录。
///
/// 只读浏览 `GET /virtual-disks/{disk_id}/browse`（磁盘内相对路径），
/// 不涉及服务端绝对路径；所有请求走 `DiskService` → `HttpService` 加密信封。
class VirtualDiskDirPicker extends ConsumerStatefulWidget {
  const VirtualDiskDirPicker({
    super.key,
    required this.disks,
    this.initialDiskId = 0,
    this.initialPath = '',
    this.title = '选择目录',
  });

  /// 可选磁盘（调用方保证都是当前账号**可写**的磁盘）
  final List<DirPickerDisk> disks;

  /// 初始选中的磁盘 ID（0 表示默认第一块）
  final int initialDiskId;

  /// 初始相对路径（空串表示磁盘根目录）
  final String initialPath;

  final String title;

  /// 以弹窗形式打开；返回 `(diskId, path)`，取消返回 null。
  static Future<({int diskId, String path})?> show(
    BuildContext context, {
    required List<DirPickerDisk> disks,
    int initialDiskId = 0,
    String initialPath = '',
    String title = '选择目录',
  }) {
    return showDialog<({int diskId, String path})>(
      context: context,
      builder: (_) => VirtualDiskDirPicker(
        disks: disks,
        initialDiskId: initialDiskId,
        initialPath: initialPath,
        title: title,
      ),
    );
  }

  @override
  ConsumerState<VirtualDiskDirPicker> createState() =>
      _VirtualDiskDirPickerState();
}

class _VirtualDiskDirPickerState extends ConsumerState<VirtualDiskDirPicker> {
  late int _diskId;
  late String _currentPath;
  final List<String> _pathStack = <String>[];
  List<Map<String, dynamic>> _dirs = const [];
  bool _loading = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _diskId = widget.initialDiskId > 0
        ? widget.initialDiskId
        : (widget.disks.isEmpty ? 0 : widget.disks.first.id);
    _currentPath = _normalize(widget.initialPath);
    _pathStack.add(_currentPath);
    if (_diskId > 0) {
      _loadDirs();
    }
  }

  /// 规范化相对路径：去首尾 `/`、折叠重复 `/`
  static String _normalize(String path) {
    final trimmed = path.trim().replaceAll(RegExp(r'^/+|/+$'), '');
    return trimmed.replaceAll(RegExp(r'/+'), '/');
  }

  Future<void> _loadDirs() async {
    if (_diskId <= 0) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final dirs = await ref
          .read(diskServiceProvider)
          .browseDirs(_diskId, path: _currentPath);
      if (!mounted) return;
      setState(() {
        _dirs = dirs;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _dirs = const [];
        _loading = false;
        _error = '目录加载失败，请重试';
      });
    }
  }

  void _onDiskChanged(int? diskId) {
    if (diskId == null) return;
    setState(() {
      _diskId = diskId;
      _currentPath = '';
      _pathStack
        ..clear()
        ..add('');
      _dirs = const [];
    });
    _loadDirs();
  }

  /// 进入子目录
  ///
  /// [serverPath] 为服务端返回的相对路径（权威）；为空时才退回用 [name] 拼接。
  void _enter(String name, {String serverPath = ''}) {
    final raw = serverPath.trim();
    final next = raw.isNotEmpty
        ? _normalize(raw)
        : (_currentPath.isEmpty ? name : '$_currentPath/$name');
    setState(() {
      _currentPath = next;
      _pathStack.add(next);
    });
    _loadDirs();
  }

  void _goBack() {
    if (_pathStack.length <= 1) return;
    setState(() {
      _pathStack.removeLast();
      _currentPath = _pathStack.last;
    });
    _loadDirs();
  }

  void _goRoot() {
    if (_currentPath.isEmpty) return;
    setState(() {
      _currentPath = '';
      _pathStack
        ..clear()
        ..add('');
    });
    _loadDirs();
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    final theme = Theme.of(context);
    return AlertDialog(
      title: Text(widget.title),
      content: SizedBox(
        width: 360,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            DropdownButtonFormField<int>(
              initialValue: _diskId > 0 ? _diskId : null,
              decoration: const InputDecoration(
                isDense: true,
                labelText: '虚拟磁盘',
                contentPadding: EdgeInsets.symmetric(
                  horizontal: 12,
                  vertical: 8,
                ),
              ),
              items: widget.disks
                  .map(
                    (d) => DropdownMenuItem<int>(
                      value: d.id,
                      child: Text(d.name),
                    ),
                  )
                  .toList(),
              onChanged: _onDiskChanged,
            ),
            const SizedBox(height: 10),
            Row(
              children: [
                Expanded(
                  child: Text(
                    '目录：/${_currentPath.isEmpty ? '' : _currentPath}',
                    style: theme.textTheme.bodySmall,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                IconButton(
                  icon: const Icon(Icons.arrow_upward, size: 18),
                  tooltip: '返回上级',
                  visualDensity: VisualDensity.compact,
                  onPressed: _pathStack.length > 1 ? _goBack : null,
                ),
                IconButton(
                  icon: const Icon(Icons.home_outlined, size: 18),
                  tooltip: '根目录',
                  visualDensity: VisualDensity.compact,
                  onPressed: _currentPath.isEmpty ? null : _goRoot,
                ),
              ],
            ),
            ConstrainedBox(
              constraints: const BoxConstraints(maxHeight: 260),
              child: _diskId <= 0
                  ? const Padding(
                      padding: EdgeInsets.symmetric(vertical: 20),
                      child: EmptyState(
                        icon: Icons.folder_off_outlined,
                        title: '没有可写磁盘',
                        subtitle: '请先联系管理员分配可写虚拟磁盘',
                      ),
                    )
                  : _loading
                  ? const Padding(
                      padding: EdgeInsets.symmetric(vertical: 20),
                      child: Center(child: CircularProgressIndicator()),
                    )
                  : _error != null
                  ? Padding(
                      padding: const EdgeInsets.symmetric(vertical: 12),
                      child: Row(
                        children: [
                          Expanded(
                            child: Text(
                              _error!,
                              style: const TextStyle(
                                fontSize: 12.5,
                                color: AppTokens.danger500,
                              ),
                            ),
                          ),
                          TextButton(onPressed: _loadDirs, child: const Text('重试')),
                        ],
                      ),
                    )
                  : _dirs.isEmpty
                  ? Padding(
                      padding: const EdgeInsets.symmetric(vertical: 20),
                      child: Text(
                        '此目录下没有子文件夹，可直接选择当前目录',
                        style: TextStyle(fontSize: 12.5, color: t.fgMuted),
                      ),
                    )
                  : ListView.builder(
                      shrinkWrap: true,
                      itemCount: _dirs.length,
                      itemBuilder: (context, index) {
                        final dir = _dirs[index];
                        final name = (dir['name'] ?? '').toString();
                        final serverPath = (dir['path'] ?? '').toString();
                        return ListTile(
                          dense: true,
                          leading: const Icon(
                            Icons.folder_outlined,
                            size: 20,
                            color: AppTokens.warning500,
                          ),
                          title: Text(
                            name.isEmpty ? '未命名目录' : name,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                          trailing: const Icon(Icons.chevron_right, size: 18),
                          onTap: () => _enter(name, serverPath: serverPath),
                        );
                      },
                    ),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('取消'),
        ),
        FilledButton(
          onPressed: _diskId <= 0
              ? null
              : () => Navigator.of(
                  context,
                ).pop((diskId: _diskId, path: _currentPath)),
          child: const Text('选择此目录'),
        ),
      ],
    );
  }
}
