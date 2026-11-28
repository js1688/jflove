/// 虚拟磁盘数据模型
///
/// 对应服务端 `GET /api/v1/files/disks` 与 `GET /api/v1/virtual-disks` 的字段。
///
/// v1.10.26（R1）：补齐容量与类型四项 —— `total_size`（最大空间）/
/// `used_size`（已用）/ `free_size`（可用）/ `disk_type`（文件系统类型）。
/// 服务端实时探测（`shutil.disk_usage`），**路径不可读时为 null**，
/// 界面统一渲染成 `—`，不要当成 0（0 会显示成 "0 B"，误导用户）。
class VirtualDisk {
  final int id;
  final String name;
  final String path;

  /// 最大空间（字节）；服务端取不到时为 null
  final int? totalSize;

  /// 已用空间（字节）；服务端取不到时为 null
  final int? usedSize;

  /// 可用空间（字节）；服务端取不到时为 null
  final int? freeSize;

  /// 文件系统类型（ext4 / xfs / btrfs / ntfs / apfs…）；取不到时为 null
  final String? diskType;

  final bool canWrite; // 当前用户是否有写权限（桌面端 v1.1.3 新增）
  final bool canDelete; // 当前用户是否有删除权限（v1.4.2 新增，修复功能需写+删并存）
  final String? createdAt;

  const VirtualDisk({
    required this.id,
    required this.name,
    this.path = '',
    this.totalSize,
    this.usedSize,
    this.freeSize,
    this.diskType,
    this.canWrite = false,
    this.canDelete = false,
    this.createdAt,
  });

  /// 解析服务端 JSON。
  ///
  /// 兼容两种历史形态：
  ///   - `files/disks`：`{id, name, can_write, can_delete, total_size…}`
  ///   - `virtual-disks`：`{id, name, real_path, created_at, total_size…}`
  factory VirtualDisk.fromJson(Map<String, dynamic> json) => VirtualDisk(
    id: json['id'] as int,
    name: json['name'] as String? ?? '',
    path: json['path'] as String? ?? json['real_path'] as String? ?? '',
    // ⚠ 不能用 `?? 0`：null（路径不可读）与 0（真没空间）语义不同
    totalSize: _toIntOrNull(json['total_size']),
    usedSize: _toIntOrNull(json['used_size']),
    freeSize: _toIntOrNull(json['free_size']),
    diskType: _toStrOrNull(json['disk_type']),
    canWrite: json['can_write'] as bool? ?? false,
    canDelete: json['can_delete'] as bool? ?? false,
    createdAt: json['created_at'] as String?,
  );

  /// 安全转 int：服务端可能下发 null，也可能是字符串数字
  static int? _toIntOrNull(Object? v) {
    if (v == null) return null;
    if (v is int) return v;
    if (v is num) return v.toInt();
    if (v is String) return int.tryParse(v);
    return null;
  }

  /// 安全转非空字符串：空串/null 一律视为"取不到"
  static String? _toStrOrNull(Object? v) {
    if (v == null) return null;
    final s = v.toString().trim();
    return s.isEmpty ? null : s;
  }

  /// 已用占比（0~1）；总容量缺失或为 0 时返回 null（不画进度条）
  double? get usedRatio {
    final total = totalSize;
    final used = usedSize;
    if (total == null || total <= 0 || used == null || used < 0) return null;
    return (used / total).clamp(0.0, 1.0);
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'name': name,
    'path': path,
    'can_write': canWrite,
    'can_delete': canDelete,
    'total_size': totalSize,
    'used_size': usedSize,
    'free_size': freeSize,
    'disk_type': diskType,
  };
}
