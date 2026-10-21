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
/// 系统级配置（离线媒体修复 + 离线下载全局策略，三端共享）收进本页，
/// 入口挂在设置页的「管理面板」分组下。
///
/// v1.8.0 UAT 裁决：离线下载默认开启，管理员可在此应急关闭（三端都要有开关）。
class AdminSystemPage extends StatelessWidget {
  const AdminSystemPage({super.key, this.configService});

  /// 仅测试注入用；生产环境为 null（卡片按登录会话创建真实服务）
  final ConfigService? configService;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('系统设置')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const MediaRepairConfigCard(),
          const SizedBox(height: 12),
          OfflineDownloadConfigCard(configService: configService),
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

// ============ 离线下载全局策略配置卡片 ============

/// 离线下载全局策略配置键（与后端 settings.py 的 OFFLINE_*_KEY 一致）
const String _offlineEnabledKey = 'offline_download_enabled';
const String _offlineDeepAnalyzeKey = 'offline_download_deep_analyze';
const String _offlineMaxConcurrentKey = 'offline_download_max_concurrent';
const String _offlineMaxFileSizeKey = 'offline_download_max_file_size_mb';
const String _offlineMaxItemsKey = 'offline_download_max_items';
const String _offlineMaxImagesKey = 'offline_download_max_images';

/// 离线下载数字项（前端只做前置提示，最终以后端 400 中文错误为准）
class _OfflineNumberField {
  final String key;
  final String label;
  final String hint;
  final String defaultValue;
  final int min;
  final int max;

  const _OfflineNumberField({
    required this.key,
    required this.label,
    required this.hint,
    required this.defaultValue,
    required this.min,
    required this.max,
  });
}

const List<_OfflineNumberField> _offlineNumberFields = [
  _OfflineNumberField(
    key: _offlineMaxConcurrentKey,
    label: '下载并发数',
    hint: '1~4',
    defaultValue: '2',
    min: 1,
    max: 4,
  ),
  _OfflineNumberField(
    key: _offlineMaxFileSizeKey,
    label: '单文件体积上限（MB）',
    hint: '1~102400',
    defaultValue: '4096',
    min: 1,
    max: 102400,
  ),
  _OfflineNumberField(
    key: _offlineMaxItemsKey,
    label: '单次清单项数上限',
    hint: '1~200',
    defaultValue: '50',
    min: 1,
    max: 200,
  ),
  _OfflineNumberField(
    key: _offlineMaxImagesKey,
    label: '单页图片张数上限',
    hint: '1~1000',
    defaultValue: '200',
    min: 1,
    max: 1000,
  ),
];

/// 离线下载全局策略配置卡片（管理员，三端共享）
///
/// 读取 `GET /api/v1/config`（未落库的键由后端补默认值），
/// 保存走 `PUT /api/v1/config`（管理员 + 加密信封），写后立即生效。
class OfflineDownloadConfigCard extends ConsumerStatefulWidget {
  const OfflineDownloadConfigCard({super.key, this.configService});

  /// 仅测试注入用；为 null 时按登录会话创建真实服务
  final ConfigService? configService;

  @override
  ConsumerState<OfflineDownloadConfigCard> createState() =>
      OfflineDownloadConfigCardState();
}

class OfflineDownloadConfigCardState
    extends ConsumerState<OfflineDownloadConfigCard> {
  bool _loading = true;
  bool _saving = false;
  bool _enabled = true;
  bool _deepAnalyze = true;
  String? _errorMsg;

  final Map<String, TextEditingController> _numberCtrls = {
    for (final field in _offlineNumberFields)
      field.key: TextEditingController(),
  };

  @override
  void initState() {
    super.initState();
    _loadConfig();
  }

  @override
  void dispose() {
    for (final controller in _numberCtrls.values) {
      controller.dispose();
    }
    super.dispose();
  }

  ConfigService _configService() {
    if (widget.configService != null) return widget.configService!;
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
        _enabled = (cfg[_offlineEnabledKey] as String? ?? '1') == '1';
        _deepAnalyze = (cfg[_offlineDeepAnalyzeKey] as String? ?? '1') == '1';
        for (final field in _offlineNumberFields) {
          _numberCtrls[field.key]!.text =
              (cfg[field.key] as String?) ?? field.defaultValue;
        }
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

  void _showMessage(String text) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  /// 开关类配置：失败时回滚界面并提示
  Future<void> _saveToggle({
    required String key,
    required bool next,
    required String label,
    required VoidCallback revert,
  }) async {
    setState(() => _saving = true);
    try {
      await _configService().updateConfig(key, next ? '1' : '0');
      if (!mounted) return;
      _showMessage('$label已${next ? '开启' : '关闭'}，立即生效');
    } catch (e) {
      if (!mounted) return;
      revert();
      _showMessage('保存失败: $e');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  /// 保存 4 个数字项（逐项 PUT /config，任一项失败即停止并提示）
  Future<void> _saveNumbers() async {
    final payload = <MapEntry<String, String>>[];
    for (final field in _offlineNumberFields) {
      final raw = _numberCtrls[field.key]!.text.trim();
      final n = int.tryParse(raw);
      if (n == null || n < field.min || n > field.max) {
        _showMessage('${field.label}需为 ${field.min}~${field.max} 的整数');
        return;
      }
      payload.add(MapEntry(field.key, '$n'));
    }
    setState(() => _saving = true);
    try {
      for (final entry in payload) {
        await _configService().updateConfig(entry.key, entry.value);
      }
      if (!mounted) return;
      _showMessage('离线下载配置已保存，立即生效');
    } catch (e) {
      if (!mounted) return;
      _showMessage('保存失败: $e');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    if (_loading) {
      return const _SysSectionCard(
        title: '离线下载',
        icon: Icons.download_outlined,
        child: Padding(
          padding: EdgeInsets.all(12),
          child: Center(child: CircularProgressIndicator()),
        ),
      );
    }
    return _SysSectionCard(
      title: '离线下载',
      icon: Icons.download_outlined,
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
            '网址分析 / 媒体抓取的全局策略，配置存于服务端、三端共享，'
            '修改后立即生效。总开关默认开启；关闭后三端入口置灰且'
            '不产生任何出站请求（应急熔断）。',
            style: TextStyle(
              fontSize: 12.5,
              height: 1.6,
              color: context.tokens.fgMuted,
            ),
          ),
          // AppCard 自带 DecoratedBox 背景；ListTile 必须放进自己的 Material，
          // 否则 debug 下会触发「ListTile background color or ink splashes may be invisible」断言。
          Material(
            type: MaterialType.transparency,
            child: SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('离线下载总开关'),
              subtitle: const Text('默认开启；关闭后三端入口置灰，不产生任何出站请求'),
              value: _enabled,
              onChanged: _saving
                  ? null
                  : (next) {
                      setState(() => _enabled = next);
                      _saveToggle(
                        key: _offlineEnabledKey,
                        next: next,
                        label: '离线下载',
                        revert: () => setState(() => _enabled = !next),
                      );
                    },
            ),
          ),
          Material(
            type: MaterialType.transparency,
            child: SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('深度分析（无头浏览器）'),
              subtitle: const Text('仅作兜底；关闭后只能靠前两级解析'),
              value: _deepAnalyze,
              onChanged: _saving
                  ? null
                  : (next) {
                      setState(() => _deepAnalyze = next);
                      _saveToggle(
                        key: _offlineDeepAnalyzeKey,
                        next: next,
                        label: '深度分析',
                        revert: () => setState(() => _deepAnalyze = !next),
                      );
                    },
            ),
          ),
          const SizedBox(height: 4),
          for (final field in _offlineNumberFields) ...[
            Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(field.label, style: theme.textTheme.bodySmall),
                      Text(
                        '允许范围 ${field.hint}',
                        style: TextStyle(
                          fontSize: 11,
                          color: context.tokens.fgMuted,
                        ),
                      ),
                    ],
                  ),
                ),
                SizedBox(
                  width: 110,
                  child: TextField(
                    controller: _numberCtrls[field.key],
                    enabled: !_saving,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(
                      isDense: true,
                      border: OutlineInputBorder(),
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
          ],
          Align(
            alignment: Alignment.centerRight,
            child: FilledButton.tonal(
              onPressed: _saving ? null : _saveNumbers,
              child: const Text('保存数值配置'),
            ),
          ),
        ],
      ),
    );
  }
}

// ============ 笔记目录配置卡片 ============

/// 笔记目录配置卡片（独立 ConsumerStatefulWidget，内部管理异步加载与对话框）
