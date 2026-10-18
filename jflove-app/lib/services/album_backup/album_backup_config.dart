import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

/// 相册自动备份配置（v1.7.0）
///
/// 按用户裁决：**配置只存客户端本地**（服务端不感知挂载/备份配置），
/// 且总开关默认为关闭。
class AlbumBackupConfig {
  /// 总开关（默认关）
  final bool enabled;

  /// 远端目标磁盘 ID（0 = 未配置）
  final int diskId;

  /// 远端目标目录（相对磁盘根，空串 = 根）
  final String remotePath;

  /// 是否备份图片
  final bool includePhotos;

  /// 是否备份视频
  final bool includeVideos;

  /// 是否仅 Wi-Fi（默认开）
  final bool wifiOnly;

  /// 是否仅充电
  final bool chargingOnly;

  const AlbumBackupConfig({
    this.enabled = false,
    this.diskId = 0,
    this.remotePath = '',
    this.includePhotos = true,
    this.includeVideos = true,
    this.wifiOnly = true,
    this.chargingOnly = false,
  });

  /// 默认配置（全部关闭）
  static const AlbumBackupConfig empty = AlbumBackupConfig();

  /// 是否已选好远端目标
  bool get isConfigured => diskId > 0;

  /// 是否有可备份的媒体类型
  bool get hasMediaType => includePhotos || includeVideos;

  Map<String, dynamic> toJson() => {
    'enabled': enabled,
    'disk_id': diskId,
    'remote_path': remotePath,
    'include_photos': includePhotos,
    'include_videos': includeVideos,
    'wifi_only': wifiOnly,
    'charging_only': chargingOnly,
  };

  factory AlbumBackupConfig.fromJson(Map<String, dynamic> json) =>
      AlbumBackupConfig(
        enabled: json['enabled'] as bool? ?? false,
        diskId: (json['disk_id'] as num?)?.toInt() ?? 0,
        remotePath: json['remote_path'] as String? ?? '',
        includePhotos: json['include_photos'] as bool? ?? true,
        includeVideos: json['include_videos'] as bool? ?? true,
        wifiOnly: json['wifi_only'] as bool? ?? true,
        chargingOnly: json['charging_only'] as bool? ?? false,
      );

  AlbumBackupConfig copyWith({
    bool? enabled,
    int? diskId,
    String? remotePath,
    bool? includePhotos,
    bool? includeVideos,
    bool? wifiOnly,
    bool? chargingOnly,
  }) => AlbumBackupConfig(
    enabled: enabled ?? this.enabled,
    diskId: diskId ?? this.diskId,
    remotePath: remotePath ?? this.remotePath,
    includePhotos: includePhotos ?? this.includePhotos,
    includeVideos: includeVideos ?? this.includeVideos,
    wifiOnly: wifiOnly ?? this.wifiOnly,
    chargingOnly: chargingOnly ?? this.chargingOnly,
  );
}

/// 配置持久化（本地 JSON 文件，按账号无关）
///
/// 说明：与旧同步配置一样存客户端本地；不写服务端、不进入任何接口。
class AlbumBackupConfigStore {
  /// 配置文件路径
  Future<String> _path() async {
    final dir = await getApplicationSupportDirectory();
    return '${dir.path}${Platform.pathSeparator}album_backup_config.json';
  }

  /// 读取配置；文件不存在或损坏时返回默认配置（不抛错，避免阻塞启动）
  Future<AlbumBackupConfig> load() async {
    try {
      final file = File(await _path());
      if (!await file.exists()) return AlbumBackupConfig.empty;
      final raw = jsonDecode(await file.readAsString());
      if (raw is! Map<String, dynamic>) return AlbumBackupConfig.empty;
      return AlbumBackupConfig.fromJson(raw);
    } catch (_) {
      return AlbumBackupConfig.empty;
    }
  }

  /// 保存配置（覆盖写）
  Future<void> save(AlbumBackupConfig config) async {
    final file = File(await _path());
    await file.parent.create(recursive: true);
    await file.writeAsString(jsonEncode(config.toJson()));
  }
}
