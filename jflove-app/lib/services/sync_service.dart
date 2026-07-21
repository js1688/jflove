import 'dart:convert';

import 'package:path_provider/path_provider.dart';
import 'dart:io';

import '../models/sync_config.dart';
import '../utils/http_service.dart';
import '../utils/session.dart';

/// 同步管理服务
/// 对标 jflove-desktop/src/services/sync_service.py
///
/// v1.2.0：同步配置文件按账号隔离，每个用户使用独立的 sync_configs_{username}.json
class SyncService {
  final HttpService _http;
  final SessionManager _session;

  SyncService(this._http, this._session);

  /// 获取当前账号对应的同步配置文件路径
  Future<String> _getConfigPath() async {
    final dir = await getApplicationDocumentsDirectory();
    final username = (_session.username).trim();
    final filename = username.isEmpty
        ? 'sync_configs_default.json'
        : 'sync_configs_$username.json';
    return '${dir.path}/$filename';
  }

  /// 加载同步配置列表（本地 JSON）
  Future<List<SyncConfig>> loadConfigs() async {
    final path = await _getConfigPath();
    final file = File(path);
    if (!await file.exists()) return [];
    final content = await file.readAsString();
    final list = jsonDecode(content) as List<dynamic>;
    return list
        .map((e) => SyncConfig.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// 保存同步配置列表
  Future<void> saveConfigs(List<SyncConfig> configs) async {
    final path = await _getConfigPath();
    final file = File(path);
    final data = jsonEncode(configs.map((e) => e.toJson()).toList());
    await file.writeAsString(data);
  }

  /// 拉取远端快照
  Future<List<Map<String, dynamic>>> fetchSnapshot(
    int diskId,
    String remotePath,
  ) async {
    final resp = await _http.encryptedPost('/api/v1/sync/snapshot', {
      'disk_id': diskId,
      'remote_path': remotePath,
    });
    return (resp['files'] as List<dynamic>).cast<Map<String, dynamic>>();
  }

  /// 手动同步
  Future<void> syncNow(int diskId, String remotePath, String localPath) async {
    await fetchSnapshot(diskId, remotePath);
    // TODO: 实现本地 diff 与下载逻辑
  }
}
