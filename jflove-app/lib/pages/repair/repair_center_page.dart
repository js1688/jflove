import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../providers/file_provider.dart';
import '../../providers/session_provider.dart';
import '../files/file_preview_page.dart';

/// 修复中心页面（v1.4.2 新增）
///
/// 展示全平台共享的媒体修复任务列表（所有登录用户可见；只读账号可看
/// 不可操作），按状态执行操作：
///   - 排队中/执行中：取消
///   - 成功：验证播放（预览修复产物）、覆盖原文件（重点二次确认）、删除产物
///   - 失败/已取消/已覆盖：删除记录
///
/// 轮询间隔 2.5s（设计文档约定）；操作权限 = 磁盘写+删并存，
/// UI 按磁盘权限禁用按钮（接口 403 兜底）。
class RepairCenterPage extends ConsumerStatefulWidget {
  const RepairCenterPage({super.key});

  @override
  ConsumerState<RepairCenterPage> createState() => _RepairCenterPageState();
}

// 任务状态中文映射
const _statusText = {
  'pending': '排队中',
  'running': '执行中',
  'verifying': '验证中',
  'success': '修复成功',
  'failed': '修复失败',
  'canceled': '已取消',
  'overridden': '已覆盖',
};

class _RepairCenterPageState extends ConsumerState<RepairCenterPage> {
  List<Map<String, dynamic>> _tasks = [];
  bool _loading = true;
  String? _error;
  Timer? _pollTimer;
  // 磁盘操作权限缓存：diskId -> 是否可操作（写+删并存）
  Map<int, bool> _canOperate = {};

  @override
  void initState() {
    super.initState();
    _refresh();
    _pollTimer = Timer.periodic(const Duration(milliseconds: 2500), (_) {
      if (mounted) _refresh();
    });
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    super.dispose();
  }

  Future<void> _refresh() async {
    try {
      final repair = ref.read(repairServiceProvider);
      final data = await repair.listTasks(page: 1, pageSize: 100);
      if (!mounted) return;
      setState(() {
        _tasks = (data['tasks'] as List<dynamic>? ?? [])
            .map((t) => t as Map<String, dynamic>)
            .toList();
        _loading = false;
        _error = null;
      });
      // 磁盘操作权限（写+删并存；admin 由服务端放行）
      final disks = await ref.read(accessibleDiskListProvider.future);
      if (mounted) {
        setState(() {
          _canOperate = {
            for (final d in disks) d.id: d.canWrite && d.canDelete,
          };
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = e.toString();
        });
      }
    }
  }

  bool _canOperateTask(Map<String, dynamic> task) {
    final isAdmin = ref.read(sessionManagerProvider).isAdmin;
    if (isAdmin) return true;
    return _canOperate[task['disk_id'] as int? ?? 0] ?? false;
  }

  String _formatSize(int size) {
    if (size < 1024) return '$size B';
    if (size < 1024 * 1024) return '${(size / 1024).toStringAsFixed(1)} KB';
    if (size < 1024 * 1024 * 1024) {
      return '${(size / 1024 / 1024).toStringAsFixed(1)} MB';
    }
    return '${(size / 1024 / 1024 / 1024).toStringAsFixed(2)} GB';
  }

  Future<void> _runOp(Future<void> Function() op, String successMsg) async {
    try {
      await op();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(successMsg)),
        );
        _refresh();
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('操作失败：$e')),
        );
      }
    }
  }

  /// 验证播放：打开修复产物预览（走 /stream?repair_task_id 加密流）
  void _verify(Map<String, dynamic> task) {
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => FilePreviewPage(
          diskId: task['disk_id'] as int? ?? 0,
          path: task['filename'] as String? ?? '',
          name: task['filename'] as String? ?? '',
          repairTaskId: task['id'] as int? ?? 0,
        ),
      ),
    );
  }

  /// 覆盖原文件：重点二次确认（原损坏文件将被删除、不可恢复）
  Future<void> _override(Map<String, dynamic> task) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('覆盖原文件（不可恢复）'),
        content: Text(
          '即将用修复产物覆盖「${task['filename']}」。\n\n'
          '⚠ 原损坏文件将被直接删除、无法恢复！\n'
          '请确认已通过「验证播放」确认修复产物可用。',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('确认覆盖'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    final repair = ref.read(repairServiceProvider);
    await _runOp(
      () => repair.overrideOrigin(task['id'] as int? ?? 0),
      '已覆盖原文件',
    );
  }

  Future<void> _confirm(
    String title,
    String text,
    Future<void> Function() op,
    String successMsg,
  ) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(title),
        content: Text(text),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('确认'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    await _runOp(op, successMsg);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('修复中心'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
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
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null && _tasks.isEmpty) {
      return Center(child: Text('加载失败：$_error'));
    }
    if (_tasks.isEmpty) {
      return Center(
        child: Text(
          '暂无修复任务\n\n对损坏的音视频文件长按 →「修复损坏媒体」发起修复',
          textAlign: TextAlign.center,
          style: TextStyle(color: Colors.grey.shade600),
        ),
      );
    }
    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView.separated(
        itemCount: _tasks.length,
        separatorBuilder: (_, _) => const Divider(height: 1),
        itemBuilder: (context, index) {
          final task = _tasks[index];
          final status = task['status'] as String? ?? '';
          final canOperate = _canOperateTask(task);
          return ListTile(
            title: Text(task['filename'] as String? ?? ''),
            subtitle: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${_statusText[status] ?? status}'
                  '${(status == 'running' || status == 'verifying')
                      ? '（${task['progress'] ?? 0}%）'
                      : ''}'
                  ' · 由 ${task['username'] ?? '?'} 发起'
                  ' · ${_formatSize(task['source_size'] as int? ?? 0)}',
                ),
                if ((task['error_message'] as String? ?? '').isNotEmpty)
                  Text(
                    task['error_message'] as String,
                    style: TextStyle(color: Colors.red.shade400, fontSize: 12),
                  ),
              ],
            ),
            trailing: _buildActions(context, task, status, canOperate),
          );
        },
      ),
    );
  }

  Widget _buildActions(
    BuildContext context,
    Map<String, dynamic> task,
    String status,
    bool canOperate,
  ) {
    final repair = ref.read(repairServiceProvider);
    final taskId = task['id'] as int? ?? 0;
    final filename = task['filename'] as String? ?? '';

    if (status == 'pending' || status == 'running' || status == 'verifying') {
      return TextButton(
        onPressed: canOperate
            ? () => _runOp(
                  () => repair.cancelTask(taskId),
                  '任务已取消',
                )
            : null,
        child: const Text('取消'),
      );
    }
    if (status == 'success') {
      return Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          TextButton(
            onPressed: () => _verify(task),
            child: const Text('验证播放'),
          ),
          PopupMenuButton<String>(
            onSelected: (v) {
              switch (v) {
                case 'override':
                  _override(task);
                case 'delete_artifact':
                  _confirm(
                    '删除修复产物',
                    '确认删除「$filename」的修复产物？',
                    () => repair.deleteArtifact(taskId),
                    '产物已删除',
                  );
              }
            },
            itemBuilder: (_) => [
              PopupMenuItem(
                value: 'override',
                enabled: canOperate,
                child: const Text('覆盖原文件'),
              ),
              PopupMenuItem(
                value: 'delete_artifact',
                enabled: canOperate,
                child: const Text('删除产物'),
              ),
            ],
          ),
        ],
      );
    }
    // 终态（failed / canceled / overridden）：删除记录
    return TextButton(
      onPressed: () => _confirm(
        '删除任务记录',
        '确认删除「$filename」的任务记录？',
        () => repair.deleteRecord(taskId),
        '记录已删除',
      ),
      child: const Text('删除记录'),
    );
  }
}
