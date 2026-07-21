/// 同步配置（本地存储）
///
/// 对标桌面端 sync_configs.json 格式。
class SyncConfig {
  final String id;
  final String name;
  final int diskId;
  final String remotePath;
  final String localPath;
  final bool autoSync;
  final int syncInterval;
  final bool enabled;
  final String? lastSyncedAt;

  const SyncConfig({
    required this.id,
    this.name = '',
    required this.diskId,
    this.remotePath = '',
    required this.localPath,
    this.autoSync = false,
    this.syncInterval = 300,
    this.enabled = true,
    this.lastSyncedAt,
  });

  Map<String, dynamic> toJson() => {
    'id': id,
    'name': name,
    'disk_id': diskId,
    'remote_path': remotePath,
    'local_path': localPath,
    'auto_sync': autoSync,
    'sync_interval': syncInterval,
    'enabled': enabled,
    'last_synced_at': lastSyncedAt,
  };

  factory SyncConfig.fromJson(Map<String, dynamic> json) => SyncConfig(
    id: json['id'] as String? ?? '',
    name: json['name'] as String? ?? '',
    diskId: json['disk_id'] as int? ?? 0,
    remotePath: json['remote_path'] as String? ?? '',
    localPath: json['local_path'] as String? ?? '',
    autoSync: json['auto_sync'] as bool? ?? false,
    syncInterval: json['sync_interval'] as int? ?? 300,
    enabled: json['enabled'] as bool? ?? true,
    lastSyncedAt: json['last_synced_at'] as String?,
  );

  SyncConfig copyWith({
    String? id,
    String? name,
    int? diskId,
    String? remotePath,
    String? localPath,
    bool? autoSync,
    int? syncInterval,
    bool? enabled,
    String? lastSyncedAt,
  }) => SyncConfig(
    id: id ?? this.id,
    name: name ?? this.name,
    diskId: diskId ?? this.diskId,
    remotePath: remotePath ?? this.remotePath,
    localPath: localPath ?? this.localPath,
    autoSync: autoSync ?? this.autoSync,
    syncInterval: syncInterval ?? this.syncInterval,
    enabled: enabled ?? this.enabled,
    lastSyncedAt: lastSyncedAt ?? this.lastSyncedAt,
  );
}
