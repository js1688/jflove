/// 磁盘权限模型
///
/// 对标桌面端权限配置中的磁盘权限对象。
class DiskPermission {
  final int id;
  final int userId;
  final int virtualDiskId;
  final bool canRead;
  final bool canWrite;
  final bool canDelete;
  final String? diskName; // 展示用

  const DiskPermission({
    this.id = 0,
    required this.userId,
    required this.virtualDiskId,
    this.canRead = false,
    this.canWrite = false,
    this.canDelete = false,
    this.diskName,
  });

  factory DiskPermission.fromJson(Map<String, dynamic> json) => DiskPermission(
    id: json['id'] as int? ?? 0,
    userId: json['user_id'] as int? ?? 0,
    virtualDiskId: json['virtual_disk_id'] as int? ?? 0,
    canRead: json['can_read'] as bool? ?? false,
    canWrite: json['can_write'] as bool? ?? false,
    canDelete: json['can_delete'] as bool? ?? false,
    diskName: json['disk_name'] as String?,
  );

  Map<String, dynamic> toJson() => {
    'id': id,
    'user_id': userId,
    'virtual_disk_id': virtualDiskId,
    'can_read': canRead,
    'can_write': canWrite,
    'can_delete': canDelete,
  };
}
