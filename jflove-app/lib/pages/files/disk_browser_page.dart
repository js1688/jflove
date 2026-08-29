import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:path_provider/path_provider.dart';

import '../../models/file_item.dart';
import '../../providers/file_provider.dart';
import '../../providers/session_provider.dart';
import '../../providers/transfer_provider.dart';
import '../../widgets/file_list_tile.dart';
import '../../widgets/loading_indicator.dart';
import '../../widgets/move_target_dialog.dart';
import '../../widgets/path_breadcrumb.dart';

class DiskBrowserPage extends ConsumerStatefulWidget {
  final int diskId;

  const DiskBrowserPage({super.key, required this.diskId});

  @override
  ConsumerState<DiskBrowserPage> createState() => _DiskBrowserPageState();
}

class _DiskBrowserPageState extends ConsumerState<DiskBrowserPage> {
  String _currentPath = '';

  void _enterDir(String name) {
    setState(() {
      _currentPath = _currentPath.isEmpty ? name : '$_currentPath/$name';
    });
  }

  void _goBack() {
    if (_currentPath.isEmpty) return;
    setState(() {
      final parts = _currentPath.split('/');
      parts.removeLast();
      _currentPath = parts.join('/');
    });
  }

  /// 刷新当前目录文件列表（用 ref.invalidate 触发 FutureProvider 重新请求）
  void _refresh() {
    ref.invalidate(
      fileListProvider((diskId: widget.diskId, path: _currentPath)),
    );
  }

  /// 计算当前用户对当前磁盘的写权限
  /// 管理员始终有写权限；普通用户读取磁盘信息中的 can_write
  bool _computeCanWrite() {
    final session = ref.read(sessionManagerProvider);
    if (session.isAdmin) return true;
    final diskListAsync = ref.read(accessibleDiskListProvider);
    bool canWrite = false;
    diskListAsync.whenData((disks) {
      for (final d in disks) {
        if (d.id == widget.diskId) {
          canWrite = d.canWrite;
          break;
        }
      }
    });
    return canWrite;
  }

  /// v1.4.2：修复功能权限 = 写+删并存（管理员天然满足）
  bool _computeCanRepair() {
    final session = ref.read(sessionManagerProvider);
    if (session.isAdmin) return true;
    final diskListAsync = ref.read(accessibleDiskListProvider);
    bool canRepair = false;
    diskListAsync.whenData((disks) {
      for (final d in disks) {
        if (d.id == widget.diskId) {
          canRepair = d.canWrite && d.canDelete;
          break;
        }
      }
    });
    return canRepair;
  }

  /// v1.4.2：可发起修复的媒体扩展名
  bool _isMediaFile(String name) {
    const exts = {
      'mp4', 'mkv', 'avi', 'mov', 'webm', 'flv', 'wmv',
      'm4v', 'mpg', 'mpeg', 'ts', '3gp',
      'mp3', 'wav', 'ogg', 'flac', 'm4a', 'aac', 'wma', 'opus',
    };
    final dot = name.lastIndexOf('.');
    if (dot < 0) return false;
    return exts.contains(name.substring(dot + 1).toLowerCase());
  }

  /// 从磁盘列表中读取当前磁盘名称（用于 AppBar 标题，替代生硬的「磁盘 #N」）
  String _diskName() {
    final diskListAsync = ref.read(accessibleDiskListProvider);
    String name = '磁盘 #${widget.diskId}'; // 兜底
    diskListAsync.whenData((disks) {
      for (final d in disks) {
        if (d.id == widget.diskId) {
          name = d.name;
          break;
        }
      }
    });
    return name;
  }

  /// 构造文件/目录在当前路径下的完整相对路径
  /// 后端 list_files 返回的每项仅含 name 不含 path，
  /// 需要客户端自行拼接当前目录和文件名
  String _itemPath(FileItem item) {
    return _currentPath.isEmpty ? item.name : '$_currentPath/${item.name}';
  }

  /// 当前所在目录的相对路径
  String get _currentRelPath => _currentPath.isEmpty ? '' : _currentPath;

  void _showFileMenu(FileItem item, bool canWrite) {
    showModalBottomSheet(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (!item.isDir) ...[
              ListTile(
                leading: const Icon(Icons.download),
                title: const Text('下载'),
                onTap: () {
                  Navigator.pop(ctx);
                  _downloadFile(item);
                },
              ),
              ListTile(
                leading: const Icon(Icons.preview),
                title: const Text('预览'),
                onTap: () {
                  Navigator.pop(ctx);
                  _previewFile(item);
                },
              ),
              // v1.4.2：修复损坏媒体（仅音视频；要求写+删权限并存）
              if (_isMediaFile(item.name))
                ListTile(
                  leading: Icon(
                    Icons.healing,
                    color: _computeCanRepair()
                        ? null
                        : Theme.of(context).disabledColor,
                  ),
                  title: Text(
                    '修复损坏媒体',
                    style: TextStyle(
                      color: _computeCanRepair()
                          ? null
                          : Theme.of(context).disabledColor,
                    ),
                  ),
                  onTap: _computeCanRepair()
                      ? () {
                          Navigator.pop(ctx);
                          _repairFile(item);
                        }
                      : null,
                ),
              const Divider(),
            ],
            ListTile(
              leading: Icon(
                Icons.edit,
                color: canWrite ? null : Theme.of(context).disabledColor,
              ),
              title: Text(
                '重命名',
                style: TextStyle(
                  color: canWrite ? null : Theme.of(context).disabledColor,
                ),
              ),
              onTap: canWrite
                  ? () {
                      Navigator.pop(ctx);
                      _renameItem(item);
                    }
                  : null,
            ),
            ListTile(
              leading: Icon(
                Icons.drive_file_move,
                color: canWrite ? null : Theme.of(context).disabledColor,
              ),
              title: Text(
                '移动到…',
                style: TextStyle(
                  color: canWrite ? null : Theme.of(context).disabledColor,
                ),
              ),
              onTap: canWrite
                  ? () {
                      Navigator.pop(ctx);
                      _moveItem(item);
                    }
                  : null,
            ),
            const Divider(),
            ListTile(
              leading: Icon(
                Icons.delete,
                color: canWrite ? Colors.red : Theme.of(context).disabledColor,
              ),
              title: Text(
                '删除',
                style: TextStyle(
                  color: canWrite
                      ? Colors.red
                      : Theme.of(context).disabledColor,
                ),
              ),
              onTap: canWrite
                  ? () {
                      Navigator.pop(ctx);
                      _deleteItem(item);
                    }
                  : null,
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _downloadFile(FileItem item) async {
    try {
      // 使用外部存储目录（Android/data/.../files/），文件管理器可访问
      // Android 上 getExternalStorageDirectory() 无需额外权限
      Directory? dir;
      try {
        dir = await getExternalStorageDirectory();
      } catch (_) {
        // 部分设备/模拟器可能不支持外部存储，回退到应用文档目录
      }
      dir ??= await getApplicationDocumentsDirectory();
      // 统一放在 jflove_downloads 子目录，方便用户查找
      final downloadDir = Directory('${dir.path}/jflove_downloads');
      if (!await downloadDir.exists()) {
        await downloadDir.create(recursive: true);
      }
      final savePath = '${downloadDir.path}/${item.name}';

      // 通过 TransferService 创建下载任务，自动跟踪进度
      final transferService = ref.read(transferServiceProvider);
      await transferService.submitDownload(
        widget.diskId,
        _itemPath(item),
        savePath,
        item.name,
        item.size,
      );

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('下载任务已添加: ${item.name}\n保存位置: $savePath'),
            duration: const Duration(seconds: 4),
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('添加下载任务失败: $e')));
      }
    }
  }

  void _previewFile(FileItem item) {
    context.push(
      '/files/preview',
      extra: {
        'disk_id': widget.diskId,
        'path': _itemPath(item),
        'name': item.name,
      },
    );
  }

  /// v1.4.2：长按菜单「修复损坏媒体」——创建异步修复任务
  Future<void> _repairFile(FileItem item) async {
    try {
      final repair = ref.read(repairServiceProvider);
      await repair.createTask(
        widget.diskId,
        _currentRelPath,
        item.name,
      );
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('已加入修复队列，可在「修复中心」查看进度')),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('发起修复失败：$e')),
        );
      }
    }
  }

  void _renameItem(FileItem item) {
    final controller = TextEditingController(text: item.name);
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('重命名'),
        content: TextField(
          controller: controller,
          decoration: const InputDecoration(labelText: '新名称'),
          autofocus: true,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () async {
              final newName = controller.text.trim();
              Navigator.pop(ctx);
              if (newName.isEmpty || newName == item.name) return;
              try {
                await ref
                    .read(fileServiceProvider)
                    .rename(widget.diskId, _itemPath(item), newName);
                _refresh();
              } catch (e) {
                if (mounted) {
                  ScaffoldMessenger.of(
                    context,
                  ).showSnackBar(SnackBar(content: Text('重命名失败: $e')));
                }
              }
            },
            child: const Text('确认'),
          ),
        ],
      ),
    );
  }

  Future<void> _moveItem(FileItem item) async {
    final srcRel = _itemPath(item);
    final dstDir = await showDialog<String>(
      context: context,
      builder: (ctx) =>
          MoveTargetDialog(diskId: widget.diskId, srcRelPath: srcRel),
    );

    if (dstDir == null) return;
    // 目标目录与当前目录相同，静默跳过
    if (dstDir == _currentRelPath) return;

    try {
      await ref.read(fileServiceProvider).move(widget.diskId, srcRel, dstDir);
      _refresh();
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('「${item.name}」已移动')));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('移动失败: $e')));
      }
    }
  }

  Future<void> _deleteItem(FileItem item) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('确认删除'),
        content: Text('确定要删除「${item.name}」吗？'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            style: FilledButton.styleFrom(
              backgroundColor: Theme.of(context).colorScheme.error,
            ),
            child: const Text('删除'),
          ),
        ],
      ),
    );

    if (confirmed == true) {
      try {
        await ref
            .read(fileServiceProvider)
            .delete(widget.diskId, _itemPath(item));
        _refresh();
      } catch (e) {
        if (mounted) {
          ScaffoldMessenger.of(
            context,
          ).showSnackBar(SnackBar(content: Text('删除失败: $e')));
        }
      }
    }
  }

  Future<void> _uploadFile() async {
    final result = await FilePicker.platform.pickFiles(allowMultiple: true);
    if (result == null || result.files.isEmpty) return;

    final transferService = ref.read(transferServiceProvider);
    int count = 0;
    for (final picked in result.files) {
      final path = picked.path;
      if (path == null) continue;
      final file = File(path);
      if (!await file.exists()) continue;
      final size = await file.length();
      final filename = path.split('/').last;
      await transferService.submitUpload(
        widget.diskId,
        _currentPath,
        path,
        filename,
        size,
      );
      count++;
    }

    if (count > 0 && mounted) {
      _refresh();
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('已加入传输队列: $count 个文件'),
          duration: const Duration(seconds: 3),
        ),
      );
    }
  }

  Future<void> _createDir() async {
    final controller = TextEditingController();
    final name = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('新建目录'),
        content: TextField(
          controller: controller,
          decoration: const InputDecoration(labelText: '目录名称'),
          autofocus: true,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, controller.text.trim()),
            child: const Text('创建'),
          ),
        ],
      ),
    );

    if (name != null && name.isNotEmpty) {
      try {
        await ref
            .read(fileServiceProvider)
            .createDir(
              widget.diskId,
              _itemPath(
                FileItem(
                  name: name,
                  path: '',
                  size: 0,
                  modifiedAt: 0,
                  isDir: true,
                ),
              ),
            );
        _refresh();
      } catch (e) {
        if (mounted) {
          ScaffoldMessenger.of(
            context,
          ).showSnackBar(SnackBar(content: Text('创建失败: $e')));
        }
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final fileListAsync = ref.watch(
      fileListProvider((diskId: widget.diskId, path: _currentPath)),
    );
    final canWrite = _computeCanWrite();
    final diskName = _diskName();

    return Scaffold(
      appBar: AppBar(
        title: Text(diskName),
        // 面包屑集成在 AppBar 底部，消除双层 header 感
        bottom: _currentPath.isNotEmpty
            ? PreferredSize(
                preferredSize: const Size.fromHeight(28),
                child: PathBreadcrumb(
                  currentPath: _currentPath,
                  onBack: _currentPath.isNotEmpty ? _goBack : null,
                ),
              )
            : null,
        actions: [
          IconButton(
            icon: const Icon(Icons.upload_file),
            tooltip: canWrite ? '上传文件' : '无写权限',
            onPressed: canWrite ? _uploadFile : null,
          ),
          IconButton(
            icon: const Icon(Icons.create_new_folder),
            tooltip: canWrite ? '新建目录' : '无写权限',
            onPressed: canWrite ? _createDir : null,
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: () async {
          _refresh();
        },
        child: fileListAsync.when(
          data: (files) {
            if (files.isEmpty) {
              return ListView(
                children: const [
                  SizedBox(height: 80),
                  Center(child: Text('此目录为空')),
                ],
              );
            }
            return ListView.builder(
              itemCount: files.length,
              itemBuilder: (ctx, i) {
                final item = files[i];
                return FileListTile(
                  item: item,
                  onTap: () {
                    if (item.isDir) {
                      _enterDir(item.name);
                    } else {
                      _previewFile(item);
                    }
                  },
                  onLongPress: () => _showFileMenu(item, canWrite),
                );
              },
            );
          },
          loading: () => const LoadingIndicator(message: '加载中…'),
          error: (e, _) => Center(child: Text('加载失败: $e')),
        ),
      ),
    );
  }
}
