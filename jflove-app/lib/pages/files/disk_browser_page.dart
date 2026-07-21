import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:path_provider/path_provider.dart';

import '../../models/file_item.dart';
import '../../providers/file_provider.dart';
import '../../widgets/file_list_tile.dart';
import '../../widgets/loading_indicator.dart';
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

  /// 构造文件/目录在当前路径下的完整相对路径
  /// 后端 list_files 返回的每项仅含 name 不含 path，
  /// 需要客户端自行拼接当前目录和文件名
  String _itemPath(FileItem item) {
    return _currentPath.isEmpty ? item.name : '$_currentPath/${item.name}';
  }

  void _showFileMenu(FileItem item) {
    showModalBottomSheet(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.download),
              title: const Text('下载'),
              onTap: () {
                Navigator.pop(ctx);
                _downloadFile(item);
              },
            ),
            if (!item.isDir) ...[
              ListTile(
                leading: const Icon(Icons.preview),
                title: const Text('预览'),
                onTap: () {
                  Navigator.pop(ctx);
                  _previewFile(item);
                },
              ),
            ],
            const Divider(),
            ListTile(
              leading: const Icon(Icons.edit),
              title: const Text('重命名'),
              onTap: () {
                Navigator.pop(ctx);
                _renameItem(item);
              },
            ),
            ListTile(
              leading: const Icon(Icons.drive_file_move),
              title: const Text('移动到…'),
              onTap: () {
                Navigator.pop(ctx);
                _moveItem(item);
              },
            ),
            const Divider(),
            ListTile(
              leading: const Icon(Icons.delete, color: Colors.red),
              title: const Text('删除', style: TextStyle(color: Colors.red)),
              onTap: () {
                Navigator.pop(ctx);
                _deleteItem(item);
              },
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _downloadFile(FileItem item) async {
    try {
      final dir = await getApplicationDocumentsDirectory();
      final savePath = '${dir.path}/${item.name}';

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
            content: Text('下载任务已添加: ${item.name}'),
            duration: const Duration(seconds: 2),
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
              Navigator.pop(ctx);
              try {
                await ref
                    .read(fileServiceProvider)
                    .rename(
                      widget.diskId,
                      _itemPath(item),
                      controller.text.trim(),
                    );
                setState(() {});
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

  void _moveItem(FileItem item) {
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(const SnackBar(content: Text('移动到…功能待实现')));
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
        setState(() {});
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
    // 简化上传：提示用户输入本地文件路径
    final controller = TextEditingController();
    final path = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('上传文件'),
        content: TextField(
          controller: controller,
          decoration: const InputDecoration(
            labelText: '文件路径',
            hintText: '/storage/emulated/0/DCIM/photo.jpg',
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, controller.text.trim()),
            child: const Text('上传'),
          ),
        ],
      ),
    );

    if (path != null && path.isNotEmpty) {
      final file = File(path);
      if (await file.exists()) {
        final transferService = ref.read(transferServiceProvider);
        await transferService.submitUpload(
          widget.diskId,
          _currentPath,
          path,
          path.split('/').last,
          await file.length(),
        );
        setState(() {});
      } else {
        if (mounted) {
          ScaffoldMessenger.of(
            context,
          ).showSnackBar(const SnackBar(content: Text('文件不存在，请检查路径')));
        }
      }
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
        final path = _currentPath.isEmpty ? name : '$_currentPath/$name';
        await ref.read(fileServiceProvider).createDir(widget.diskId, path);
        setState(() {});
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

    return Scaffold(
      appBar: AppBar(
        title: Text('磁盘 #${widget.diskId}'),
        actions: [
          IconButton(
            icon: const Icon(Icons.upload_file),
            tooltip: '上传文件',
            onPressed: _uploadFile,
          ),
          IconButton(
            icon: const Icon(Icons.create_new_folder),
            tooltip: '新建目录',
            onPressed: _createDir,
          ),
        ],
      ),
      body: Column(
        children: [
          PathBreadcrumb(
            currentPath: _currentPath,
            onBack: _currentPath.isNotEmpty ? _goBack : null,
          ),
          Expanded(
            child: RefreshIndicator(
              onRefresh: () async {
                setState(() {});
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
                        onLongPress: () => _showFileMenu(item),
                      );
                    },
                  );
                },
                loading: () => const LoadingIndicator(message: '加载中…'),
                error: (e, _) => Center(child: Text('加载失败: $e')),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
