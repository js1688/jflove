import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../config/design_tokens.dart';
import '../../services/config_service.dart';
import '../../utils/http_service.dart';
import '../../providers/session_provider.dart';
import '../../widgets/app_card.dart';

/// 系统设置（admin）
///
/// **层级对齐 Web 端**：Web 端侧栏的「管理」分组是
/// 用户管理 / 磁盘管理 / 权限配置 / **系统设置**；App 端此前把
/// 「媒体修复」这类**系统级配置**直接放在了「设置」一级页面里，
/// 既与 Web 端层级不一致，也让"个人设置"与"服务端共享配置"混在一起。
///
/// 现在：设置页只保留个人向内容（服务器 / 安全状态 / 账号 / 外观 / 关于），
/// 系统级配置（离线媒体修复，三端共享）收进本页，入口挂在设置页的「管理面板」分组下。
class AdminSystemPage extends StatelessWidget {
  const AdminSystemPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('系统设置')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: const [
          MediaRepairConfigCard(),
        ],
      ),
    );
  }
}

class _SysSectionCard extends StatelessWidget {
  final String title;
  final IconData icon;
  final Widget child;

  const _SysSectionCard({
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

class MediaRepairConfigCard extends ConsumerStatefulWidget {
  const MediaRepairConfigCard({super.key});

  @override
  ConsumerState<MediaRepairConfigCard> createState() =>
      MediaRepairConfigCardState();
}

class MediaRepairConfigCardState
    extends ConsumerState<MediaRepairConfigCard> {
  bool _loading = true;
  bool _saving = false;
  bool _allowTranscode = false;
  final TextEditingController _concurrentCtrl = TextEditingController();
  String? _errorMsg;

  @override
  void initState() {
    super.initState();
    _loadConfig();
  }

  @override
  void dispose() {
    _concurrentCtrl.dispose();
    super.dispose();
  }

  ConfigService _configService() {
    final session = ref.read(sessionManagerProvider);
    return ConfigService(HttpService(session));
  }

  Future<void> _loadConfig() async {
    setState(() {
      _loading = true;
      _errorMsg = null;
    });
    try {
      final resp = await _configService().getConfig();
      final cfg = (resp['config'] as Map<String, dynamic>?) ?? {};
      if (!mounted) return;
      setState(() {
        _allowTranscode = cfg['media_repair_allow_transcode'] == '1';
        _concurrentCtrl.text =
            (cfg['media_repair_max_concurrent'] as String?) ?? '';
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _errorMsg = '$e';
      });
    }
  }

  Future<void> _save(String key, String value) async {
    setState(() => _saving = true);
    try {
      await _configService().updateConfig(key, value);
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('配置已保存，立即生效')));
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('保存失败: $e')));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  void _toggleTranscode(bool next) {
    setState(() => _allowTranscode = next);
    _save('media_repair_allow_transcode', next ? '1' : '0');
  }

  void _saveConcurrent() {
    final raw = _concurrentCtrl.text.trim();
    if (raw.isEmpty) {
      _save('media_repair_max_concurrent', '');
      return;
    }
    final n = int.tryParse(raw);
    if (n != null && n >= 1 && n <= 8) {
      _save('media_repair_max_concurrent', '$n');
    } else {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('并发数需为 1~8 的整数，或留空使用自动基线')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    if (_loading) {
      return const _SysSectionCard(
        title: '媒体修复',
        icon: Icons.healing_outlined,
        child: Padding(
          padding: EdgeInsets.all(12),
          child: Center(child: CircularProgressIndicator()),
        ),
      );
    }
    return _SysSectionCard(
      title: '离线媒体修复',
      icon: Icons.healing_outlined,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (_errorMsg != null) ...[
            Text(
              '加载失败: $_errorMsg',
              style: const TextStyle(
                color: AppTokens.danger500,
                fontSize: 12,
              ),
            ),
            const SizedBox(height: 8),
          ],
          Text(
            'v1.4.2 起损坏媒体经「修复中心」手动离线修复（文件列表长按'
            '「修复损坏媒体」发起）。以下为修复队列配置：并发数 1~8 或留空'
            '按服务器 CPU 核数自动推导；重编码为无损修复失败时的降级手段。',
            style: TextStyle(
              fontSize: 12.5,
              height: 1.6,
              color: context.tokens.fgMuted,
            ),
          ),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('允许重编码降级'),
            subtitle: const Text('默认关闭；-c copy 失败时的极端兜底'),
            value: _allowTranscode,
            onChanged: _saving ? null : _toggleTranscode,
          ),
          const SizedBox(height: 4),
          Row(
            children: [
              Expanded(
                child: Text(
                  '修复并发数（1~8，留空自动）',
                  style: theme.textTheme.bodySmall,
                ),
              ),
              SizedBox(
                width: 90,
                child: TextField(
                  controller: _concurrentCtrl,
                  enabled: !_saving,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                    isDense: true,
                    hintText: '自动',
                    border: OutlineInputBorder(),
                  ),
                ),
              ),
              const SizedBox(width: 8),
              FilledButton.tonal(
                onPressed: _saving ? null : _saveConcurrent,
                child: const Text('保存'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

// ============ 笔记目录配置卡片 ============

/// 笔记目录配置卡片（独立 ConsumerStatefulWidget，内部管理异步加载与对话框）
