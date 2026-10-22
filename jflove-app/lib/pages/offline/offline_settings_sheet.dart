import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../config/design_tokens.dart';
import '../../models/offline_task.dart';
import '../../providers/offline_provider.dart';
import '../../utils/exception.dart';
import '../../widgets/virtual_disk_dir_picker.dart';

/// 打开「离线下载设置」底部弹层（v1.8.1 / F32.4、AC-249）
///
/// 保存 / 清除账号级默认目录（磁盘 + 磁盘内相对路径），服务端存储，
/// 桌面 / Web 同账号可见同一份。
Future<void> showOfflineSettingsSheet(
  BuildContext context, {
  required OfflineMeta meta,
  required Future<void> Function() onSaved,
}) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    showDragHandle: true,
    builder: (_) => OfflineSettingsSheet(meta: meta, onSaved: onSaved),
  );
}

/// 默认目录设置弹层
class OfflineSettingsSheet extends ConsumerStatefulWidget {
  const OfflineSettingsSheet({
    super.key,
    required this.meta,
    required this.onSaved,
  });

  final OfflineMeta meta;
  final Future<void> Function() onSaved;

  @override
  ConsumerState<OfflineSettingsSheet> createState() =>
      _OfflineSettingsSheetState();
}

class _OfflineSettingsSheetState extends ConsumerState<OfflineSettingsSheet> {
  late int _diskId;
  late String _relPath;
  bool _create = false;
  bool _saving = false;
  String? _error;

  List<OfflineWritableDisk> get _disks => widget.meta.writableDisks;

  @override
  void initState() {
    super.initState();
    _diskId = widget.meta.configDiskId > 0
        ? widget.meta.configDiskId
        : (_disks.isEmpty ? 0 : _disks.first.diskId);
    _relPath = widget.meta.configRelPath;
  }

  String get _diskName {
    for (final disk in _disks) {
      if (disk.diskId == _diskId) return disk.name;
    }
    return '未选择磁盘';
  }

  Future<void> _pickDir() async {
    final picked = await VirtualDiskDirPicker.show(
      context,
      disks: _disks
          .map((d) => DirPickerDisk(id: d.diskId, name: d.name))
          .toList(growable: false),
      initialDiskId: _diskId,
      initialPath: _relPath,
      title: '选择默认目录',
    );
    if (picked == null || !mounted) return;
    setState(() {
      _diskId = picked.diskId;
      _relPath = picked.path;
    });
  }

  Future<void> _save() async {
    if (_diskId <= 0) {
      setState(() => _error = '请先选择虚拟磁盘');
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await ref
          .read(offlineDownloadServiceProvider)
          .saveSettings(diskId: _diskId, relPath: _relPath, create: _create);
      await widget.onSaved();
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('默认目录已保存')));
      Navigator.of(context).pop();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _saving = false;
        _error = userFacingError(e, fallback: '保存默认目录失败');
      });
    }
  }

  Future<void> _clear() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await ref
          .read(offlineDownloadServiceProvider)
          .saveSettings(diskId: 0);
      await widget.onSaved();
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('默认目录已清除')));
      Navigator.of(context).pop();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _saving = false;
        _error = userFacingError(e, fallback: '清除默认目录失败');
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    final noDisk = _disks.isEmpty;
    return Padding(
      padding: EdgeInsets.fromLTRB(
        16,
        0,
        16,
        MediaQuery.viewInsetsOf(context).bottom + 16,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.settings_outlined, size: 20),
              SizedBox(width: t.s2),
              const Expanded(
                child: Text(
                  '离线下载设置',
                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
                ),
              ),
              IconButton(
                icon: const Icon(Icons.close_rounded),
                tooltip: '关闭',
                onPressed: () => Navigator.of(context).pop(),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            '默认目录按账号保存在服务端，桌面端 / Web 端同账号可见同一份。',
            style: TextStyle(fontSize: 12, color: t.fgMuted, height: 1.4),
          ),
          const SizedBox(height: 12),
          if (noDisk)
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: AppTokens.warning500.withValues(alpha: 0.10),
                borderRadius: BorderRadius.circular(t.rMd),
              ),
              child: const Text(
                '当前账号没有可写入的磁盘，无法配置默认目录。请联系管理员分配写权限。',
                style: TextStyle(fontSize: 12.5, height: 1.4),
              ),
            )
          else ...[
            DropdownButtonFormField<int>(
              initialValue: _diskId > 0 ? _diskId : null,
              decoration: const InputDecoration(
                isDense: true,
                labelText: '虚拟磁盘',
              ),
              items: _disks
                  .map(
                    (d) => DropdownMenuItem<int>(
                      value: d.diskId,
                      child: Text(d.name),
                    ),
                  )
                  .toList(),
              onChanged: _saving
                  ? null
                  : (v) {
                      if (v != null) setState(() => _diskId = v);
                    },
            ),
            const SizedBox(height: 12),
            Text('当前目录', style: TextStyle(fontSize: 12, color: t.fgMuted)),
            const SizedBox(height: 4),
            Row(
              children: [
                Expanded(
                  child: Text(
                    '$_diskName / ${_relPath.isEmpty ? '磁盘根目录' : _relPath}',
                    style: const TextStyle(fontSize: 13.5),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                OutlinedButton.icon(
                  icon: const Icon(Icons.folder_open_outlined, size: 16),
                  label: const Text('选择目录'),
                  onPressed: _saving ? null : _pickDir,
                ),
              ],
            ),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              dense: true,
              title: const Text('目录不存在时自动创建'),
              subtitle: const Text('默认关闭；开启后保存时服务端会创建该目录'),
              value: _create,
              onChanged: _saving
                  ? null
                  : (v) => setState(() => _create = v),
            ),
          ],
          if (_error != null) ...[
            const SizedBox(height: 8),
            Text(
              _error!,
              style: const TextStyle(fontSize: 12.5, color: AppTokens.danger500),
            ),
          ],
          const SizedBox(height: 12),
          Row(
            children: [
              TextButton(
                onPressed: _saving || noDisk ? null : _clear,
                child: const Text('清除默认目录'),
              ),
              const Spacer(),
              FilledButton(
                onPressed: _saving || noDisk ? null : _save,
                child: _saving
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('保存'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
