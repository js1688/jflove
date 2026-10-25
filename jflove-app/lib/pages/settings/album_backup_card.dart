import 'dart:async';

import 'package:device_info_plus/device_info_plus.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../config/design_tokens.dart';
import '../../models/virtual_disk.dart';
import '../../providers/album_backup_provider.dart';
import '../../providers/file_provider.dart';
import '../../providers/session_provider.dart';
import '../../services/album_backup/album_backup_config.dart';
import '../../services/album_backup/album_backup_service.dart';
import '../../widgets/app_card.dart';
import '../../widgets/virtual_disk_dir_picker.dart';

/// 相册自动同步设置卡（v1.7.0）
///
/// 位置：设置页「笔记目录配置」之后、「账号」之前。
/// 约束：总开关默认关闭；远端目标必须由用户显式选择；配置只存客户端本地。
class AlbumBackupCard extends ConsumerStatefulWidget {
  const AlbumBackupCard({super.key});

  @override
  ConsumerState<AlbumBackupCard> createState() => _AlbumBackupCardState();
}

class _AlbumBackupCardState extends ConsumerState<AlbumBackupCard> {
  AlbumBackupConfig _config = AlbumBackupConfig.empty;
  Map<String, int> _typeCounts = const {};
  bool _loading = true;
  bool _busy = false;
  Timer? _progressTimer;

  @override
  void initState() {
    super.initState();
    _load();
    // issue #6 ②：数量统计要**定时刷新**（上传完成不一定经过本页事件循环）。
    _progressTimer = Timer.periodic(const Duration(seconds: 5), (_) {
      if (!mounted) return;
      unawaited(ref.read(albumBackupServiceProvider).emitProgress());
      unawaited(_refreshTypeCounts());
    });
  }

  @override
  void dispose() {
    _progressTimer?.cancel();
    _progressTimer = null;
    super.dispose();
  }

  Future<void> _load() async {
    final service = ref.read(albumBackupServiceProvider);
    await service.init();
    if (!mounted) return;
    final counts = await service.typeCounts();
    if (!mounted) return;
    setState(() {
      _config = service.config;
      _typeCounts = counts;
      _loading = false;
    });
  }

  Future<void> _refreshTypeCounts() async {
    try {
      final counts = await ref.read(albumBackupServiceProvider).typeCounts();
      if (!mounted) return;
      setState(() => _typeCounts = counts);
    } catch (_) {
      // 统计失败不影响主流程
    }
  }

  Future<void> _apply(AlbumBackupConfig next) async {
    setState(() => _busy = true);
    try {
      await ref.read(albumBackupServiceProvider).setConfig(next);
      if (mounted) {
        setState(() => _config = next);
        // 传输页的摘要依赖该 Provider；配置变更后必须失效，否则会一直显示旧状态。
        ref.invalidate(albumBackupConfigProvider);
      }
    } catch (e) {
      _snack('保存失败：$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _toggle(bool value) async {
    if (value) {
      if (!_config.isConfigured) {
        _snack('请先选择远端目标目录');
        return;
      }
      final granted = await ref
          .read(albumBackupServiceProvider)
          .ensurePermission();
      if (!granted) {
        _snack('未获得相册读取权限，请在系统设置中允许后重试');
        return;
      }
    }
    await _apply(_config.copyWith(enabled: value));
  }

  Future<void> _pickTarget() async {
    List<VirtualDisk> disks;
    try {
      disks = await ref.read(accessibleDiskListProvider.future);
    } catch (e) {
      _snack('读取磁盘列表失败：$e');
      return;
    }
    if (!mounted) return;
    if (disks.isEmpty) {
      _snack('没有可用的磁盘');
      return;
    }
    // v1.8.1：远端目录改为「磁盘 + 目录选择器」，不再要求手写子路径
    final picked = await VirtualDiskDirPicker.show(
      context,
      disks: disks
          .map((d) => DirPickerDisk(id: d.id, name: d.name))
          .toList(growable: false),
      initialDiskId: _config.diskId,
      initialPath: _config.remotePath,
      title: '选择远端目标目录',
    );
    if (picked == null) return;
    await _apply(
      _config.copyWith(diskId: picked.diskId, remotePath: picked.path),
    );
  }

  Future<void> _showBackgroundHint() async {
    var brand = '';
    try {
      final info = await DeviceInfoPlugin().androidInfo;
      brand = info.manufacturer.toLowerCase();
    } catch (_) {
      brand = '';
    }
    if (!mounted) return;

    String body;
    if (brand.contains('xiaomi') || brand.contains('redmi')) {
      body =
          '小米/Redmi：设置 → 应用设置 → 应用管理 → JFLove → '
          '省电策略选「无限制」，并打开「自启动」。';
    } else if (brand.contains('huawei') || brand.contains('honor')) {
      body =
          '华为/荣耀：设置 → 应用 → 应用启动管理 → JFLove → '
          '改为「手动管理」，允许自启动/关联启动/后台活动。';
    } else if (brand.contains('oppo') || brand.contains('realme')) {
      body =
          'OPPO/realme：设置 → 电池 → 应用耗电管理 → JFLove → '
          '允许后台运行与自启动。';
    } else if (brand.contains('vivo') || brand.contains('iqoo')) {
      body =
          'vivo/iQOO：设置 → 电池 → 后台耗电管理 → JFLove → '
          '允许后台高耗电；并在 i管家允许自启动。';
    } else {
      body =
          '若备份在后台中断，请在系统设置中允许本应用「自启动 / 后台运行」，'
          '并关闭针对本应用的电池优化。';
    }
    await showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('后台运行设置'),
        content: Text(body),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('知道了'),
          ),
        ],
      ),
    );
  }

  void _snack(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
  }

  /// 展开失败原因（中文、不含路径；区分永久不可用 / 暂时失败）
  Future<void> _showFailureReasons() async {
    final entries = await ref.read(albumBackupServiceProvider).failedEntries();
    if (!mounted) return;
    await showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('失败原因（${entries.length} 项）'),
        content: entries.isEmpty
            ? const Text('暂无失败记录')
            : SizedBox(
                width: double.maxFinite,
                child: ListView.builder(
                  shrinkWrap: true,
                  itemCount: entries.length,
                  itemBuilder: (_, index) {
                    final entry = entries[index];
                    final kind = entry.permanent ? '永久不可用' : '暂时失败';
                    return ListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      title: Text(entry.reason),
                      subtitle: Text('$kind · 已尝试 ${entry.attempts} 次'),
                    );
                  },
                ),
              ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('关闭'),
          ),
        ],
      ),
    );
  }

  String _lastScanText(AlbumBackupService service) {
    final at = service.lastScanAt;
    final uploaded = service.lastScanUploaded;
    final skipped = service.lastScanSkipped;
    final when = at == null
        ? '尚未检测'
        : '${at.month.toString().padLeft(2, '0')}-'
              '${at.day.toString().padLeft(2, '0')} '
              '${at.hour.toString().padLeft(2, '0')}:'
              '${at.minute.toString().padLeft(2, '0')}';
    return '上次检测：$when · 新增上传 $uploaded 项 · 跳过 $skipped 项 · 每 60 秒自动检测';
  }

  String _indexText(AlbumBackupService service) {
    final at = service.remoteIndexBuiltAt;
    if (at == null) return '远端索引：尚未建立（上传前会先建立）';
    final hh = at.hour.toString().padLeft(2, '0');
    final mm = at.minute.toString().padLeft(2, '0');
    return '远端索引：$hh:$mm 建立 · 最多 10 分钟刷新一次（可手动全量核对）';
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    final session = ref.watch(sessionManagerProvider);
    final progressAsync = ref.watch(albumBackupProgressProvider);

    return AppCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 26,
                height: 26,
                decoration: BoxDecoration(
                  color: t.bgActive,
                  borderRadius: BorderRadius.circular(t.rSm),
                ),
                child: const Icon(
                  Icons.photo_library_outlined,
                  size: 15,
                  color: AppTokens.brand600,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  '相册自动同步',
                  style: TextStyle(
                    fontSize: 14.5,
                    fontWeight: FontWeight.w600,
                    color: t.fgDefault,
                  ),
                ),
              ),
              if (_busy)
                const SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            '开启后，新的照片/视频会在满足条件时自动上传到你指定的远端目录。'
            '只在客户端保存配置，服务端不记录。',
            style: TextStyle(fontSize: 12, color: t.fgSubtle, height: 1.5),
          ),
          if (session.isAdmin) ...[
            const SizedBox(height: 8),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
              decoration: BoxDecoration(
                color: t.bgSunken,
                borderRadius: BorderRadius.circular(t.rSm),
              ),
              child: Text(
                '当前为管理员账号：管理员可写入所有磁盘，请确认目标目录合适。',
                style: TextStyle(fontSize: 12, color: t.fgMuted),
              ),
            ),
          ],
          const SizedBox(height: 8),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('自动同步相册'),
            subtitle: const Text('默认关闭；关闭后不再产生新的上传任务'),
            value: _config.enabled,
            onChanged: _loading ? null : (v) => _toggle(v),
          ),
          const Divider(height: 1),
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.folder_outlined, size: 20),
            title: const Text('远端目标目录'),
            subtitle: Text(
              _config.isConfigured
                  ? '磁盘 #${_config.diskId} · '
                        '${_config.remotePath.isEmpty ? '根目录' : _config.remotePath}'
                  : '未选择',
            ),
            trailing: TextButton(
              onPressed: _loading ? null : _pickTarget,
              child: const Text('选择'),
            ),
          ),
          const Divider(height: 1),
          _SwitchRow(
            label: '备份图片',
            value: _config.includePhotos,
            onChanged: (v) => _apply(_config.copyWith(includePhotos: v)),
          ),
          _SwitchRow(
            label: '备份视频',
            value: _config.includeVideos,
            onChanged: (v) => _apply(_config.copyWith(includeVideos: v)),
          ),
          _SwitchRow(
            label: '仅 Wi-Fi',
            value: _config.wifiOnly,
            onChanged: (v) => _apply(_config.copyWith(wifiOnly: v)),
          ),
          _SwitchRow(
            label: '仅充电时',
            value: _config.chargingOnly,
            onChanged: (v) => _apply(_config.copyWith(chargingOnly: v)),
          ),
          const Divider(height: 1),
          progressAsync.when(
            data: (p) => Padding(
              padding: const EdgeInsets.fromLTRB(0, 12, 0, 12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '同步明细',
                    style: TextStyle(
                      fontSize: 13,
                      fontWeight: FontWeight.w600,
                      color: t.fgDefault,
                    ),
                  ),
                  const SizedBox(height: 8),
                  _SyncTable(
                    rows: [
                      _SyncRowData(
                        icon: Icons.image_outlined,
                        label: '图片',
                        total: _typeCounts['imageTotal'] ?? 0,
                        done: _typeCounts['imageDone'] ?? 0,
                      ),
                      _SyncRowData(
                        icon: Icons.videocam_outlined,
                        label: '视频',
                        total: _typeCounts['videoTotal'] ?? 0,
                        done: _typeCounts['videoDone'] ?? 0,
                      ),
                    ],
                    totalColor: t.fgDefault,
                    subtleColor: t.fgSubtle,
                  ),
                  const SizedBox(height: 14),
                  Text(
                    '任务状态',
                    style: TextStyle(
                      fontSize: 13,
                      fontWeight: FontWeight.w600,
                      color: t.fgDefault,
                    ),
                  ),
                  const SizedBox(height: 4),
                  _KeyValueRow(label: '待备份', value: '${p.pending} 项'),
                  _KeyValueRow(label: '上传中', value: '${p.uploading} 项'),
                  _KeyValueRow(label: '失败', value: '${p.failed} 项'),
                  _KeyValueRow(label: '不在范围', value: '${p.outOfScope} 项'),
                  _KeyValueRow(
                    label: '远端已存在跳过',
                    value: '${p.skippedExisting} 项',
                  ),
                  _KeyValueRow(
                    label: '等待远端索引（已暂停）',
                    value: '${p.remoteDeferred} 项',
                  ),
                  const SizedBox(height: 6),
                  Text(
                    _lastScanText(ref.read(albumBackupServiceProvider)),
                    style: TextStyle(fontSize: 11.5, color: t.fgSubtle),
                  ),
                  Text(
                    _indexText(ref.read(albumBackupServiceProvider)),
                    style: TextStyle(fontSize: 11.5, color: t.fgSubtle),
                  ),
                  if (p.remoteCheckUnavailable) ...[
                    const SizedBox(height: 6),
                    Container(
                      width: double.infinity,
                      padding: const EdgeInsets.symmetric(
                        horizontal: 10,
                        vertical: 8,
                      ),
                      decoration: BoxDecoration(
                        color: AppTokens.warning500.withValues(alpha: 0.12),
                        borderRadius: BorderRadius.circular(t.rSm),
                      ),
                      child: const Text(
                        '远端索引不可用：这些目录已暂停上传（不会盲传），下轮自动重试；请检查网络/服务端后点「全量核对远端」。',
                        style: TextStyle(
                          fontSize: 12,
                          color: AppTokens.warning500,
                        ),
                      ),
                    ),
                  ],
                ],
              ),
            ),
            loading: () => const Padding(
              padding: EdgeInsets.symmetric(vertical: 10),
              child: Text('读取备份进度…'),
            ),
            error: (e, _) => Padding(
              padding: const EdgeInsets.symmetric(vertical: 10),
              child: Text('读取备份进度失败：$e'),
            ),
          ),
          const Divider(height: 1),
          Padding(
            padding: const EdgeInsets.only(top: 12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  '操作',
                  style: TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w600,
                    color: t.fgDefault,
                  ),
                ),
                const SizedBox(height: 8),
                FilledButton.icon(
                  onPressed: _loading
                      ? null
                      : () async {
                          await ref
                              .read(albumBackupServiceProvider)
                              .checkNow();
                          if (mounted) _snack('已触发一次检查');
                        },
                  icon: const Icon(Icons.sync, size: 18),
                  label: const Text('立即检查并上传'),
                ),
                const SizedBox(height: 8),
                Row(
                  children: [
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: _loading
                            ? null
                            : () async {
                                await ref
                                    .read(albumBackupServiceProvider)
                                    .retryFailed();
                                if (mounted) _snack('已重新排队失败项');
                              },
                        icon: const Icon(Icons.replay, size: 18),
                        label: const Text('重试失败项'),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: _loading
                            ? null
                            : () async {
                                await ref
                                    .read(albumBackupServiceProvider)
                                    .recheckRemote();
                                if (mounted) _snack('已开始全量核对远端');
                              },
                        icon: const Icon(Icons.cloud_sync_outlined, size: 18),
                        label: const Text('全量核对远端'),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 4),
                Row(
                  children: [
                    if ((progressAsync.asData?.value.failed ?? 0) > 0)
                      Expanded(
                        child: TextButton.icon(
                          onPressed: _showFailureReasons,
                          icon: const Icon(Icons.error_outline, size: 18),
                          label: const Text('查看失败原因'),
                        ),
                      ),
                    Expanded(
                      child: TextButton.icon(
                        onPressed: _showBackgroundHint,
                        icon: const Icon(Icons.info_outline, size: 18),
                        label: const Text('后台运行设置'),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              OutlinedButton.icon(
                icon: const Icon(Icons.sync, size: 18),
                label: const Text('立即检查'),
                onPressed: _loading
                    ? null
                    : () async {
                        await ref.read(albumBackupServiceProvider).checkNow();
                        if (mounted) _snack('已触发一次检查');
                      },
              ),
              OutlinedButton.icon(
                icon: const Icon(Icons.replay, size: 18),
                label: const Text('重试失败项'),
                onPressed: _loading
                    ? null
                    : () async {
                        await ref
                            .read(albumBackupServiceProvider)
                            .retryFailed();
                        if (mounted) _snack('已重新排队失败项');
                      },
              ),
              OutlinedButton.icon(
                icon: const Icon(Icons.cloud_sync_outlined, size: 18),
                label: const Text('重新核对远端'),
                onPressed: _loading
                    ? null
                    : () async {
                        await ref
                            .read(albumBackupServiceProvider)
                            .recheckRemote();
                        if (mounted) _snack('已重新核对远端目录');
                      },
              ),
              if ((progressAsync.asData?.value.failed ?? 0) > 0)
                TextButton.icon(
                  icon: const Icon(Icons.error_outline, size: 18),
                  label: const Text('查看失败原因'),
                  onPressed: _showFailureReasons,
                ),
              TextButton.icon(
                icon: const Icon(Icons.info_outline, size: 18),
                label: const Text('后台运行设置'),
                onPressed: _showBackgroundHint,
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// 单个开关行
class _SwitchRow extends StatelessWidget {
  final String label;
  final bool value;
  final ValueChanged<bool> onChanged;

  const _SwitchRow({
    required this.label,
    required this.value,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    return SwitchListTile(
      contentPadding: EdgeInsets.zero,
      dense: true,
      title: Text(label),
      value: value,
      onChanged: onChanged,
    );
  }
}


/// 同步明细表（v1.8.3：表单化展示，避免文案挤在一起）
class _SyncRowData {
  final IconData icon;
  final String label;
  final int total;
  final int done;

  const _SyncRowData({
    required this.icon,
    required this.label,
    required this.total,
    required this.done,
  });
}

class _SyncTable extends StatelessWidget {
  final List<_SyncRowData> rows;
  final Color totalColor;
  final Color subtleColor;

  const _SyncTable({
    required this.rows,
    required this.totalColor,
    required this.subtleColor,
  });

  @override
  Widget build(BuildContext context) {
    return Table(
      columnWidths: const {
        0: FlexColumnWidth(1.4),
        1: FlexColumnWidth(1),
        2: FlexColumnWidth(1),
        3: FlexColumnWidth(1),
      },
      children: [
        TableRow(
          children: [
            _cell('类型', subtleColor, bold: true),
            _cell('总数', subtleColor, bold: true, align: TextAlign.right),
            _cell('已同步', subtleColor, bold: true, align: TextAlign.right),
            _cell('待同步', subtleColor, bold: true, align: TextAlign.right),
          ],
        ),
        for (final row in rows)
          TableRow(
            children: [
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: Row(
                  children: [
                    Icon(row.icon, size: 16, color: totalColor),
                    const SizedBox(width: 6),
                    Text(
                      row.label,
                      style: TextStyle(fontSize: 12.5, color: totalColor),
                    ),
                  ],
                ),
              ),
              _cell('${row.total}', totalColor, align: TextAlign.right),
              _cell('${row.done}', totalColor, align: TextAlign.right),
              _cell(
                '${row.total > row.done ? row.total - row.done : 0}',
                subtleColor,
                align: TextAlign.right,
              ),
            ],
          ),
      ],
    );
  }

  Widget _cell(
    String text,
    Color color, {
    bool bold = false,
    TextAlign align = TextAlign.left,
  }) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Text(
        text,
        textAlign: align,
        style: TextStyle(
          fontSize: 12.5,
          color: color,
          fontWeight: bold ? FontWeight.w600 : FontWeight.normal,
        ),
      ),
    );
  }
}

/// 两列表单行（标签左、数值右）
class _KeyValueRow extends StatelessWidget {
  final String label;
  final String value;

  const _KeyValueRow({required this.label, required this.value});

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          Expanded(
            child: Text(
              label,
              style: TextStyle(fontSize: 12.5, color: t.fgMuted),
            ),
          ),
          Text(
            value,
            style: TextStyle(fontSize: 12.5, color: t.fgDefault),
          ),
        ],
      ),
    );
  }
}
