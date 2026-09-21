import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/design_tokens.dart';
import '../models/file_item.dart';
import '../providers/file_provider.dart';
import '../widgets/app_card.dart';
import '../widgets/empty_state.dart';
import '../widgets/error_state.dart';
import '../widgets/path_breadcrumb.dart';

/// 移动目标目录选择弹窗
///
/// 对标桌面端 `MoveTargetDialog`。
/// 展示当前磁盘的目录树（仅目录），用户选定后返回目标目录的相对路径。
/// 采用「逐级浏览」交互（点击进入子目录、返回上级），更适合移动端。
///
/// v1.5.0：视觉层接入设计令牌 —— 选中态由 `primaryContainer` 换成
/// `t.bgActive`（品牌淡底），文件夹图标用 `AppTokens.warning500`，
/// 空态 / 加载态 / 错误态换成统一组件；浏览与选择逻辑与 v1.4.2 完全一致。
class MoveTargetDialog extends ConsumerStatefulWidget {
  final int diskId;

  /// 被移动项的相对路径（用于禁止移动到自身）
  final String srcRelPath;

  const MoveTargetDialog({
    super.key,
    required this.diskId,
    required this.srcRelPath,
  });

  @override
  ConsumerState<MoveTargetDialog> createState() => _MoveTargetDialogState();
}

class _MoveTargetDialogState extends ConsumerState<MoveTargetDialog> {
  /// 当前浏览到的目录路径（相对路径，根目录为空字符串）
  String _currentPath = '';

  /// 路径栈，用于返回上级
  final List<String> _pathStack = [''];

  /// 用户选定的目标目录路径（null 表示未选定，选定根目录为空字符串）
  String? _selectedPath;

  String get _srcTopName =>
      widget.srcRelPath.isEmpty ? '' : widget.srcRelPath.split('/').first;

  /// 判断某个目录是否是被移动项自身或其子目录（禁止选为目标）
  bool _isSrcOrChild(String dirPath) {
    if (widget.srcRelPath.isEmpty) return false;
    // 被移动项的顶层名
    final srcTop = _srcTopName;
    // 当前路径的顶层
    final dirTop = dirPath.isEmpty ? '' : dirPath.split('/').first;
    // 如果当前目录的顶层就是被移动项的顶层，说明在同一棵子树下
    return srcTop.isNotEmpty && srcTop == dirTop;
  }

  void _enterDir(String name) {
    final current = _pathStack.last;
    final newPath = '$current/$name'.replaceAll(RegExp(r'^/+'), '');
    _pathStack.add(newPath);
    setState(() {
      _currentPath = newPath;
      _selectedPath = newPath;
    });
  }

  void _goBack() {
    if (_pathStack.length <= 1) return;
    _pathStack.removeLast();
    setState(() {
      _currentPath = _pathStack.last;
      _selectedPath = _currentPath;
    });
  }

  void _selectRoot() {
    setState(() {
      _currentPath = '';
      _selectedPath = '';
    });
  }

  /// 当前浏览路径对应的文件列表 provider 参数（重试时复用同一参数）
  ({int diskId, String path}) _fileListArg() =>
      (diskId: widget.diskId, path: _currentPath);

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    // 根目录不能是被移动项的子树
    final rootDisabled = _isSrcOrChild('');

    return AlertDialog(
      titlePadding: EdgeInsets.fromLTRB(t.s5, t.s5, t.s5, 0),
      // 标题：品牌淡底图标块 + 文案（与统一卡片的分区标题观感一致）
      title: Row(
        children: [
          Container(
            width: 32,
            height: 32,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              gradient: t.gradBrandSoft,
              borderRadius: BorderRadius.circular(t.rMd),
              border: Border.all(color: t.borderSubtle),
            ),
            child: const Icon(
              Icons.drive_file_move_outline,
              size: 17,
              color: AppTokens.brand600,
            ),
          ),
          SizedBox(width: t.s3),
          const Expanded(child: Text('选择目标目录')),
        ],
      ),
      contentPadding: EdgeInsets.fromLTRB(0, t.s4, 0, 0),
      content: SizedBox(
        width: double.maxFinite,
        height: 420,
        child: Column(
          children: [
            // 面包屑导航
            PathBreadcrumb(
              currentPath: _currentPath,
              onBack: _pathStack.length > 1 ? _goBack : null,
            ),
            const Divider(height: 1),
            // 目录列表
            Expanded(child: _buildDirList()),
            const Divider(height: 1),
            // 根目录选择按钮
            InkWell(
              onTap: rootDisabled ? null : _selectRoot,
              child: Container(
                width: double.infinity,
                padding: EdgeInsets.symmetric(horizontal: t.s4, vertical: t.s3),
                color: _selectedPath == '' && !rootDisabled ? t.bgActive : null,
                child: Row(
                  children: [
                    Icon(
                      Icons.home_rounded,
                      size: 20,
                      color: rootDisabled ? t.fgSubtle : AppTokens.brand500,
                    ),
                    SizedBox(width: t.s3),
                    Expanded(
                      child: Text(
                        '根目录 /',
                        style: TextStyle(
                          fontSize: 13.5,
                          color: rootDisabled ? t.fgSubtle : t.fgDefault,
                        ),
                      ),
                    ),
                    if (_selectedPath == '' && !rootDisabled)
                      const Icon(
                        Icons.check_rounded,
                        size: 20,
                        color: AppTokens.brand500,
                      ),
                  ],
                ),
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
        FilledButton(
          onPressed: _selectedPath != null
              ? () => Navigator.pop(context, _selectedPath)
              : null,
          child: const Text('确认移动'),
        ),
      ],
    );
  }

  Widget _buildDirList() {
    final dirListAsync = ref.watch(fileListProvider(_fileListArg()));

    return dirListAsync.when(
      data: (files) {
        // 只显示目录，过滤掉文件
        final dirs = files.where((f) => f.isDir).toList();
        if (dirs.isEmpty) {
          return const EmptyState(
            icon: Icons.folder_off_outlined,
            title: '此目录下无子目录',
          );
        }
        return ListView.builder(
          itemCount: dirs.length,
          itemBuilder: (ctx, i) => _DirListTile(
            item: dirs[i],
            selected: _selectedPath == _fullPath(dirs[i].name),
            disabled: _isSrcOrChild(_fullPath(dirs[i].name)),
            onTap: () => _enterDir(dirs[i].name),
          ),
        );
      },
      loading: () => const ListSkeleton(rows: 4),
      error: (e, _) => ErrorState(
        message: '$e',
        onRetry: () => ref.invalidate(fileListProvider(_fileListArg())),
      ),
    );
  }

  /// 拼接当前路径 + 目录名
  String _fullPath(String name) {
    return '$_currentPath/$name'.replaceAll(RegExp(r'^/+'), '');
  }
}

/// 目录列表项
class _DirListTile extends StatelessWidget {
  final FileItem item;
  final bool selected;
  final bool disabled;
  final VoidCallback onTap;

  const _DirListTile({
    required this.item,
    required this.selected,
    required this.disabled,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    return InkWell(
      onTap: disabled ? null : onTap,
      child: Container(
        padding: EdgeInsets.symmetric(horizontal: t.s4, vertical: t.s3),
        color: selected ? t.bgActive : null,
        child: Row(
          children: [
            Icon(
              Icons.folder_rounded,
              size: 20,
              // 文件夹用警示黄（与文件列表的目录图标语义一致）
              color: disabled ? t.fgSubtle : AppTokens.warning500,
            ),
            SizedBox(width: t.s3),
            Expanded(
              child: Text(
                item.name,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontSize: 13.5,
                  color: disabled ? t.fgSubtle : t.fgDefault,
                ),
              ),
            ),
            if (selected)
              const Icon(
                Icons.check_rounded,
                size: 20,
                color: AppTokens.brand500,
              )
            else if (!disabled)
              Icon(Icons.chevron_right_rounded, size: 20, color: t.fgSubtle),
          ],
        ),
      ),
    );
  }
}
