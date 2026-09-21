import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../config/design_tokens.dart';
import '../../providers/session_provider.dart';
import '../../providers/theme_provider.dart';
import '../../services/auth_service.dart';
import '../../services/note_service.dart';
import '../../services/disk_service.dart';
import '../../utils/http_service.dart';
import '../../widgets/app_card.dart';
import '../../widgets/empty_state.dart';

/// 设置页
class SettingsPage extends ConsumerWidget {
  const SettingsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final session = ref.watch(sessionManagerProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('设置')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          // ---- 服务器 ----
          _SectionCard(
            title: '服务器',
            icon: Icons.dns,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _InfoRow(
                  label: '当前地址',
                  value: session.serverUrl.isEmpty ? '未设置' : session.serverUrl,
                ),
                const SizedBox(height: 12),
                SizedBox(
                  width: double.infinity,
                  child: FilledButton.tonalIcon(
                    icon: const Icon(Icons.edit, size: 18),
                    label: const Text('修改服务器地址'),
                    onPressed: () => _showServerUrlDialog(context, ref),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 12),
          // ---- 安全状态 ----
          _SectionCard(
            title: '安全状态',
            icon: Icons.lock_outline,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _StatusRow(
                  icon: Icons.wifi,
                  label: '会话状态',
                  value: session.isSessionReady ? '已加密' : '未加密',
                  isActive: session.isSessionReady,
                ),
                if (session.sessionId.isNotEmpty) ...[
                  const SizedBox(height: 4),
                  _InfoRow(
                    label: 'Session ID',
                    value:
                        '${session.sessionId.substring(0, session.sessionId.length > 8 ? 8 : session.sessionId.length)}...',
                  ),
                ],
                if (session.keyExchangeTime > 0) ...[
                  const SizedBox(height: 4),
                  _InfoRow(
                    label: '密钥交换',
                    value:
                        '${(DateTime.now().millisecondsSinceEpoch / 1000 - session.keyExchangeTime).round() ~/ 60} 分钟前',
                  ),
                ],
                const SizedBox(height: 12),
                SizedBox(
                  width: double.infinity,
                  child: OutlinedButton.icon(
                    icon: const Icon(Icons.refresh, size: 18),
                    label: const Text('刷新会话密钥'),
                    onPressed: () async {
                      try {
                        final http = HttpService(session);
                        final authService = AuthService(http, session);
                        await authService.keyExchange();
                        if (context.mounted) {
                          _showSuccessSnackBar(context, '会话密钥已刷新');
                        }
                      } catch (e) {
                        if (context.mounted) {
                          _showErrorSnackBar(context, '刷新失败: $e');
                        }
                      }
                    },
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 12),
          // ---- 外观（v1.5.0 新增：三态主题切换） ----
          const _ThemeModeCard(),
          const SizedBox(height: 12),
          // ---- 笔记目录配置 ----
          _NotesDirConfigCard(),
          const SizedBox(height: 12),
          // ---- 账号 ----
          _SectionCard(
            title: '账号',
            icon: Icons.person_outline,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _InfoRow(label: '用户名', value: session.username),
                const SizedBox(height: 4),
                _StatusRow(
                  icon: Icons.badge,
                  label: '角色',
                  value: session.role == 'admin' ? '管理员' : '普通用户',
                  isActive: true,
                ),
                if (session.tokenExpiresAt > 0) ...[
                  const SizedBox(height: 4),
                  _InfoRow(
                    label: 'Token 过期',
                    value: DateTime.fromMillisecondsSinceEpoch(
                      (session.tokenExpiresAt * 1000).toInt(),
                    ).toString().substring(0, 19),
                  ),
                ],
                const SizedBox(height: 12),
                SizedBox(
                  width: double.infinity,
                  child: OutlinedButton.icon(
                    icon: const Icon(
                      Icons.logout,
                      size: 18,
                      color: AppTokens.danger500,
                    ),
                    label: const Text(
                      '退出登录',
                      style: TextStyle(color: AppTokens.danger500),
                    ),
                    onPressed: () async {
                      final http = HttpService(session);
                      final authService = AuthService(http, session);
                      await authService.logout();
                      if (context.mounted) {
                        context.go('/login');
                      }
                    },
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 12),
          // ---- 管理面板（仅 admin） ----
          if (session.isAdmin) ...[
            _SectionCard(
              title: '管理面板',
              icon: Icons.admin_panel_settings_outlined,
              child: Column(
                children: [
                  _AdminListTile(
                    icon: Icons.people_outline,
                    title: '用户管理',
                    onTap: () => context.push('/admin/users'),
                  ),
                  const Divider(height: 1, indent: 16),
                  _AdminListTile(
                    icon: Icons.cloud_outlined,
                    title: '磁盘管理',
                    onTap: () => context.push('/admin/disks'),
                  ),
                  const Divider(height: 1, indent: 16),
                  _AdminListTile(
                    icon: Icons.security_outlined,
                    title: '权限配置',
                    onTap: () => context.push('/admin/permissions'),
                  ),
                  const Divider(height: 1, indent: 16),
                  _AdminListTile(
                    icon: Icons.tune,
                    title: '系统设置',
                    onTap: () => context.push('/admin/system'),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 12),
                  const SizedBox(height: 12),
          ],
          // ---- 关于 ----
          _SectionCard(
            title: '关于',
            icon: Icons.info_outline,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _InfoRow(label: '版本', value: 'v1.5.0'),
                const SizedBox(height: 6),
                Text(
                  '私有文档 & 笔记管理系统',
                  style: TextStyle(fontSize: 12.5, color: context.tokens.fgMuted),
                ),
                const SizedBox(height: 4),
                Text(
                  'X25519 ECDH + ChaCha20-Poly1305 + ES256 JWT',
                  style: TextStyle(
                    fontSize: 11,
                    color: context.tokens.fgSubtle,
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 32),
        ],
      ),
    );
  }

  /// 修改服务器地址对话框
  Future<void> _showServerUrlDialog(BuildContext context, WidgetRef ref) async {
    final session = ref.read(sessionManagerProvider);
    final controller = TextEditingController(text: session.serverUrl);

    final newUrl = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('修改服务器地址'),
        content: TextField(
          controller: controller,
          decoration: const InputDecoration(
            labelText: '服务器地址',
            hintText: 'http://192.168.1.100:8989',
            prefixIcon: Icon(Icons.dns),
          ),
          autofocus: true,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, controller.text.trim()),
            child: const Text('确定'),
          ),
        ],
      ),
    );

    if (newUrl != null && newUrl.isNotEmpty && newUrl != session.serverUrl) {
      session.serverUrl = newUrl;
      await session.saveToStorage();
      ref.invalidate(sessionManagerProvider);
      if (context.mounted) {
        _showSuccessSnackBar(context, '服务器地址已更新');
      }
    }
  }

  void _showSuccessSnackBar(BuildContext context, String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Row(
          children: [
            const Icon(
              Icons.check_circle_outline,
              color: Colors.white,
              size: 20,
            ),
            const SizedBox(width: 12),
            Expanded(child: Text(message)),
          ],
        ),
        behavior: SnackBarBehavior.floating,
        margin: const EdgeInsets.fromLTRB(16, 0, 16, 16),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
        duration: const Duration(seconds: 3),
      ),
    );
  }

  void _showErrorSnackBar(BuildContext context, String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Row(
          children: [
            const Icon(Icons.error_outline, color: Colors.white, size: 20),
            const SizedBox(width: 12),
            Expanded(child: Text(message)),
          ],
        ),
        behavior: SnackBarBehavior.floating,
        margin: const EdgeInsets.fromLTRB(16, 0, 16, 16),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
        duration: const Duration(seconds: 3),
      ),
    );
  }
}

// ============ 子组件 ============

/// 外观卡片：三态主题切换（v1.5.0 新增）
///
/// 与桌面端托盘「主题」子菜单、Web 端 ThemeToggle 语义一致：
/// 跟随系统 / 亮色 / 暗色。选择会立即生效并持久化（`themeModeProvider`）。
class _ThemeModeCard extends ConsumerWidget {
  const _ThemeModeCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.tokens;
    final current = ref.watch(themeModeProvider);

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
                  Icons.brightness_6_outlined,
                  size: 15,
                  color: AppTokens.brand600,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  '外观',
                  style: TextStyle(
                    fontSize: 14.5,
                    fontWeight: FontWeight.w600,
                    color: t.fgDefault,
                  ),
                ),
              ),
              AppBadge(current.label, tone: BadgeTone.brand),
            ],
          ),
          const SizedBox(height: 12),
          // 三选一分段控件：跟随系统 / 亮色 / 暗色
          Container(
            padding: const EdgeInsets.all(3),
            decoration: BoxDecoration(
              color: t.bgSunken,
              borderRadius: BorderRadius.circular(t.rMd),
            ),
            child: Row(
              children: [
                for (final mode in AppThemeMode.values)
                  Expanded(
                    child: _ThemeModeOption(
                      mode: mode,
                      selected: mode == current,
                      onTap: () => ref
                          .read(themeModeProvider.notifier)
                          .setMode(mode),
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// 外观分段控件的单个选项
class _ThemeModeOption extends StatelessWidget {
  const _ThemeModeOption({
    required this.mode,
    required this.selected,
    required this.onTap,
  });

  final AppThemeMode mode;
  final bool selected;
  final VoidCallback onTap;

  /// 每个模式对应的图标（语义化，避免只用文字）
  IconData get _icon {
    switch (mode) {
      case AppThemeMode.system:
        return Icons.brightness_auto_outlined;
      case AppThemeMode.light:
        return Icons.light_mode_outlined;
      case AppThemeMode.dark:
        return Icons.dark_mode_outlined;
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(t.rSm),
        child: AnimatedContainer(
          duration: t.durFast,
          padding: const EdgeInsets.symmetric(vertical: 9),
          decoration: BoxDecoration(
            color: selected ? t.bgSurface : Colors.transparent,
            borderRadius: BorderRadius.circular(t.rSm),
            boxShadow: selected ? t.e1 : null,
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(
                _icon,
                size: 15,
                color: selected ? AppTokens.brand600 : t.fgMuted,
              ),
              const SizedBox(width: 6),
              Text(
                mode.label,
                style: TextStyle(
                  fontSize: 12.5,
                  fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
                  color: selected ? t.fgDefault : t.fgMuted,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// 带图标的分组卡片（v1.5.0：改用统一 AppCard + 设计令牌）
class _SectionCard extends StatelessWidget {
  final String title;
  final IconData icon;
  final Widget child;

  const _SectionCard({
    required this.title,
    required this.icon,
    required this.child,
  });

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
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
                child: Icon(icon, size: 15, color: AppTokens.brand600),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  title,
                  style: TextStyle(
                    fontSize: 14.5,
                    fontWeight: FontWeight.w600,
                    color: t.fgDefault,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          child,
        ],
      ),
    );
  }
}

/// 信息行（标签 + 值）

class _InfoRow extends StatelessWidget {
  final String label;
  final String value;

  const _InfoRow({required this.label, required this.value});

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          width: 90,
          child: Text(
            label,
            style: TextStyle(fontSize: 12.5, color: t.fgMuted),
          ),
        ),
        Expanded(
          child: Text(
            value,
            style: TextStyle(fontSize: 13.5, color: t.fgDefault, height: 1.4),
          ),
        ),
      ],
    );
  }
}

/// 状态行（带状态徽标）
class _StatusRow extends StatelessWidget {
  final IconData icon;
  final String label;
  final String value;
  final bool isActive;

  const _StatusRow({
    required this.icon,
    required this.label,
    required this.value,
    required this.isActive,
  });

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    return Row(
      children: [
        SizedBox(
          width: 90,
          child: Text(
            label,
            style: TextStyle(fontSize: 12.5, color: t.fgMuted),
          ),
        ),
        AppBadge(
          value,
          icon: icon,
          tone: isActive ? BadgeTone.success : BadgeTone.neutral,
        ),
      ],
    );
  }
}

/// 管理面板列表项
class _AdminListTile extends StatelessWidget {
  final IconData icon;
  final String title;
  final VoidCallback onTap;

  const _AdminListTile({
    required this.icon,
    required this.title,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return ListTile(
      leading: Icon(icon),
      title: Text(title),
      trailing: const Icon(Icons.chevron_right, size: 20),
      onTap: onTap,
      contentPadding: const EdgeInsets.symmetric(horizontal: 4),
    );
  }
}

class _NotesDirConfigCard extends ConsumerStatefulWidget {
  @override
  ConsumerState<_NotesDirConfigCard> createState() =>
      _NotesDirConfigCardState();
}

class _NotesDirConfigCardState extends ConsumerState<_NotesDirConfigCard> {
  /// 当前配置的快照，null 表示加载中/未配置
  _NotesDiskSnapshot? _snapshot;
  bool _loading = true;
  String? _errorMsg;

  @override
  void initState() {
    super.initState();
    _loadConfig();
  }

  Future<void> _loadConfig() async {
    setState(() {
      _loading = true;
      _errorMsg = null;
    });
    try {
      final session = ref.read(sessionManagerProvider);
      final http = HttpService(session);
      final noteService = NoteService(http);
      final result = await noteService.getNotesDiskConfig();
      final disks =
          (result['disks'] as List<dynamic>?)
              ?.map((e) => e as Map<String, dynamic>)
              .toList() ??
          [];
      final diskId = result['disk_id'] as int?;
      final path = result['path'] as String? ?? '';
      setState(() {
        _snapshot = _NotesDiskSnapshot(
          diskId: diskId,
          path: path,
          disks: disks,
        );
        _loading = false;
      });
    } catch (e) {
      setState(() {
        _errorMsg = e.toString();
        _loading = false;
      });
    }
  }

  String _formatCurrent() {
    if (_snapshot == null) return '未配置';
    if (_snapshot!.diskId == null) return '未配置';
    final diskName = _snapshot!.disks
        .where((d) => d['id'] == _snapshot!.diskId)
        .map((d) => d['name'] as String)
        .firstOrNull;
    final diskLabel = diskName ?? '磁盘#${_snapshot!.diskId}';
    final pathLabel = _snapshot!.path.isEmpty ? '（根目录）' : _snapshot!.path;
    return '$diskLabel / $pathLabel';
  }

  Future<void> _openConfigDialog() async {
    final session = ref.read(sessionManagerProvider);
    final http = HttpService(session);
    final noteService = NoteService(http);
    final diskService = DiskService(http);

    // 传入当前快照，若未加载则重新获取
    final snapshot = _snapshot ?? await _fetchSnapshot(noteService);
    if (!mounted) return;

    final result = await showDialog<_NotesDiskSnapshot>(
      context: context,
      builder: (ctx) => _NotesDirDialog(
        initial: snapshot,
        diskService: diskService,
        noteService: noteService,
      ),
    );

    if (result != null && mounted) {
      try {
        await noteService.setNotesDiskConfig(result.diskId, path: result.path);
        setState(() => _snapshot = result);
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('笔记目录已设置为：${_formatCurrent()}')),
          );
        }
      } catch (e) {
        if (mounted) {
          ScaffoldMessenger.of(
            context,
          ).showSnackBar(SnackBar(content: Text('保存失败: $e')));
        }
      }
    }
  }

  Future<_NotesDiskSnapshot> _fetchSnapshot(NoteService noteService) async {
    final result = await noteService.getNotesDiskConfig();
    final disks =
        (result['disks'] as List<dynamic>?)
            ?.map((e) => e as Map<String, dynamic>)
            .toList() ??
        [];
    return _NotesDiskSnapshot(
      diskId: result['disk_id'] as int?,
      path: result['path'] as String? ?? '',
      disks: disks,
    );
  }

  @override
  Widget build(BuildContext context) {
    return _SectionCard(
      title: '笔记目录',
      icon: Icons.menu_book_outlined,
      child: _loading
          ? const SizedBox(
              height: 24,
              child: Center(child: LinearProgressIndicator()),
            )
          : Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _InfoRow(label: '当前配置', value: _formatCurrent()),
                if (_errorMsg != null) ...[
                  const SizedBox(height: 4),
                  Text(
                    '加载失败: $_errorMsg',
                    style: const TextStyle(
                      fontSize: 12,
                      color: AppTokens.danger500,
                    ),
                  ),
                ],
                const SizedBox(height: 12),
                SizedBox(
                  width: double.infinity,
                  child: FilledButton.tonalIcon(
                    icon: const Icon(Icons.edit, size: 18),
                    label: const Text('配置笔记目录'),
                    onPressed: _openConfigDialog,
                  ),
                ),
              ],
            ),
    );
  }
}

/// 笔记目录配置快照
class _NotesDiskSnapshot {
  final int? diskId;
  final String path;
  final List<Map<String, dynamic>> disks;

  const _NotesDiskSnapshot({
    this.diskId,
    this.path = '',
    this.disks = const [],
  });
}

/// 笔记目录配置对话框
class _NotesDirDialog extends StatefulWidget {
  final _NotesDiskSnapshot initial;
  final DiskService diskService;
  final NoteService noteService;

  const _NotesDirDialog({
    required this.initial,
    required this.diskService,
    required this.noteService,
  });

  @override
  State<_NotesDirDialog> createState() => _NotesDirDialogState();
}

class _NotesDirDialogState extends State<_NotesDirDialog> {
  late int? _selectedDiskId;
  late String _selectedPath;
  late List<Map<String, dynamic>> _disks;
  List<Map<String, dynamic>> _subdirs = [];
  List<String> _pathStack = [''];
  bool _loadingDirs = false;

  @override
  void initState() {
    super.initState();
    _selectedDiskId = widget.initial.diskId;
    _selectedPath = widget.initial.path;
    _disks = widget.initial.disks;
    _pathStack = [_selectedPath.isEmpty ? '' : _selectedPath];
    if (_selectedDiskId != null) {
      _loadSubdirs();
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
      _pathStack = [''];
      _selectedPath = '';
    });
    if (diskId != null) _loadSubdirs();
  }

  void _enterDir(String dirPath) {
    setState(() {
      _pathStack.add(dirPath);
    });
    _loadSubdirs();
  }

  void _goBack() {
    if (_pathStack.length > 1) {
      setState(() {
        _pathStack.removeLast();
      });
      _loadSubdirs();
    }
  }

  void _goRoot() {
    setState(() {
      _pathStack = [''];
    });
    _loadSubdirs();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final currentPath = _pathStack.last;
    return AlertDialog(
      title: const Text('配置笔记目录'),
      content: SizedBox(
        width: 360,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('第 1 步：选择虚拟磁盘'),
            const SizedBox(height: 4),
            DropdownButtonFormField<int>(
              initialValue: _selectedDiskId,
              decoration: const InputDecoration(
                isDense: true,
                contentPadding: EdgeInsets.symmetric(
                  horizontal: 12,
                  vertical: 8,
                ),
              ),
              hint: const Text('选择磁盘'),
              items: _disks.map((d) {
                final id = d['id'] as int;
                return DropdownMenuItem<int>(
                  value: id,
                  child: Text(d['name'] as String),
                );
              }).toList(),
              onChanged: _onDiskChanged,
            ),
            const SizedBox(height: 12),
            const Text('第 2 步：浏览并选择子目录'),
            const SizedBox(height: 4),
            Row(
              children: [
                Expanded(
                  child: Text(
                    '路径: /${currentPath.isEmpty ? "" : currentPath}',
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
                ? Padding(
                    padding: const EdgeInsets.symmetric(vertical: 20),
                    child: EmptyState(
                      icon: Icons.folder_off_outlined,
                      title: '请先选择虚拟磁盘',
                      subtitle: '选择磁盘后可浏览并挑选笔记目录',
                    ),
                  )
                : _loadingDirs
                ? const ListSkeleton(rows: 3)
                : _subdirs.isEmpty
                ? const Padding(
                    padding: EdgeInsets.symmetric(vertical: 20),
                    child: EmptyState(
                      icon: Icons.folder_open_outlined,
                      title: '此目录下没有子文件夹',
                      subtitle: '可直接使用当前目录作为笔记目录',
                    ),
                  )
                : SizedBox(
                    height: 180,
                    child: ListView.builder(
                      itemCount: _subdirs.length,
                      itemBuilder: (ctx, i) {
                        final dir = _subdirs[i];
                        return ListTile(
                          dense: true,
                          leading: const Icon(Icons.folder_outlined, size: 20),
                          title: Text(dir['name'] as String),
                          onTap: () => _enterDir(dir['path'] as String),
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
        FilledButton(
          onPressed: () {
            if (_selectedDiskId == null) return;
            Navigator.pop(
              context,
              _NotesDiskSnapshot(
                diskId: _selectedDiskId,
                path: currentPath,
                disks: _disks,
              ),
            );
          },
          child: const Text('确定'),
        ),
      ],
    );
  }
}
