import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/design_tokens.dart';
import '../providers/file_provider.dart';
import '../widgets/empty_state.dart';

/// 服务器目录选择弹窗（v1.8.1 / D14、AC-257）
///
/// 用于「添加 / 编辑虚拟磁盘」时选择服务端真实路径。后端接口
/// `GET /api/v1/virtual-disks/server-dirs` 仅管理员可用、只列目录。
///
/// 交互：roots 快捷入口 + 上级 / 根目录 + 逐级进入 + 选择当前目录。
class ServerDirPickerDialog extends ConsumerStatefulWidget {
  const ServerDirPickerDialog({super.key, this.initialPath = ''});

  /// 打开时定位的绝对路径；空串由服务端从 roots 第一项开始
  final String initialPath;

  /// 以弹窗形式打开；返回选中的绝对路径，取消返回 null。
  static Future<String?> show(
    BuildContext context, {
    String initialPath = '',
  }) {
    return showDialog<String>(
      context: context,
      builder: (_) => ServerDirPickerDialog(initialPath: initialPath),
    );
  }

  @override
  ConsumerState<ServerDirPickerDialog> createState() =>
      _ServerDirPickerDialogState();
}

class _ServerDirPickerDialogState extends ConsumerState<ServerDirPickerDialog> {
  String _currentPath = '';
  String? _parent;
  List<Map<String, dynamic>> _dirs = const [];
  List<Map<String, dynamic>> _roots = const [];
  bool _truncated = false;
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _currentPath = widget.initialPath.trim();
    _load(_currentPath);
  }

  Future<void> _load(String path) async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final data = await ref
          .read(diskServiceProvider)
          .browseServerDirs(path: path);
      if (!mounted) return;
      final dirs = (data['dirs'] as List<dynamic>? ?? const [])
          .whereType<Map>()
          .map((e) => Map<String, dynamic>.from(e))
          .toList(growable: false);
      final roots = (data['roots'] as List<dynamic>? ?? const [])
          .whereType<Map>()
          .map((e) => Map<String, dynamic>.from(e))
          .toList(growable: false);
      setState(() {
        _currentPath = (data['path'] ?? path).toString();
        final parent = data['parent'];
        _parent = parent is String && parent.trim().isNotEmpty ? parent : null;
        _dirs = dirs;
        _roots = roots;
        _truncated = data['truncated'] == true;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _dirs = const [];
        _loading = false;
        // 不回显服务端完整路径；错误文案保持简短
        _error = '目录加载失败，请确认路径可访问后重试';
      });
    }
  }

  void _enter(String path) => _load(path);

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    return AlertDialog(
      title: const Text('选择服务器目录'),
      content: SizedBox(
        width: 380,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    '路径：${_currentPath.isEmpty ? '（加载中…）' : _currentPath}',
                    style: Theme.of(context).textTheme.bodySmall,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                IconButton(
                  icon: const Icon(Icons.arrow_upward, size: 18),
                  tooltip: '上级',
                  visualDensity: VisualDensity.compact,
                  onPressed: _parent == null ? null : () => _load(_parent!),
                ),
                IconButton(
                  icon: const Icon(Icons.home_outlined, size: 18),
                  tooltip: '根目录',
                  visualDensity: VisualDensity.compact,
                  onPressed: _roots.isEmpty
                      ? null
                      : () => _load((_roots.first['path'] ?? '').toString()),
                ),
              ],
            ),
            if (_roots.isNotEmpty)
              Wrap(
                spacing: 6,
                runSpacing: 2,
                children: _roots
                    .map(
                      (root) => ActionChip(
                        label: Text(
                          (root['name'] ?? root['path'] ?? '').toString(),
                        ),
                        onPressed: () =>
                            _load((root['path'] ?? '').toString()),
                      ),
                    )
                    .toList(),
              ),
            if (_truncated)
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Text(
                  '目录过多，仅显示前 2000 项，请进入更具体的路径。',
                  style: TextStyle(fontSize: 12, color: t.fgMuted),
                ),
              ),
            const SizedBox(height: 6),
            ConstrainedBox(
              constraints: const BoxConstraints(maxHeight: 280),
              child: _loading
                  ? const Padding(
                      padding: EdgeInsets.symmetric(vertical: 24),
                      child: Center(child: CircularProgressIndicator()),
                    )
                  : _error != null
                  ? Padding(
                      padding: const EdgeInsets.symmetric(vertical: 14),
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
                          TextButton(
                            onPressed: () => _load(_currentPath),
                            child: const Text('重试'),
                          ),
                        ],
                      ),
                    )
                  : _dirs.isEmpty
                  ? const Padding(
                      padding: EdgeInsets.symmetric(vertical: 18),
                      child: EmptyState(
                        icon: Icons.folder_off_outlined,
                        title: '此目录下没有子文件夹',
                        subtitle: '可直接选择当前目录',
                      ),
                    )
                  : ListView.builder(
                      shrinkWrap: true,
                      itemCount: _dirs.length,
                      itemBuilder: (context, index) {
                        final dir = _dirs[index];
                        final name = (dir['name'] ?? '').toString();
                        final path = (dir['path'] ?? '').toString();
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
                          onTap: path.isEmpty ? null : () => _enter(path),
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
          onPressed: _loading || _error != null || _currentPath.isEmpty
              ? null
              : () => Navigator.of(context).pop(_currentPath),
          child: const Text('选择此目录'),
        ),
      ],
    );
  }
}
