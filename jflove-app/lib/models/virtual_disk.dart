/// 虚拟磁盘数据模型
///
/// 对应桌面端 file_page.py 中磁盘信息的字段：id, name, real_path, can_write, created_at
class VirtualDisk {
  final int id;
  final String name;
  final String path;
  final int totalSize;
  final int usedSize;
  final bool canWrite; // 当前用户是否有写权限（桌面端 v1.1.3 新增）
  final bool canDelete; // 当前用户是否有删除权限（v1.4.2 新增，修复功能需写+删并存）
  final String? createdAt;

  const VirtualDisk({
    required this.id,
    required this.name,
    this.path = '',
    this.totalSize = 0,
    this.usedSize = 0,
    this.canWrite = false,
    this.canDelete = false,
    this.createdAt,
  });

  factory VirtualDisk.fromJson(Map<String, dynamic> json) => VirtualDisk(
    id: json['id'] as int,
    name: json['name'] as String? ?? '',
    path: json['path'] as String? ?? json['real_path'] as String? ?? '',
    totalSize: json['total_size'] as int? ?? 0,
    usedSize: json['used_size'] as int? ?? 0,
    canWrite: json['can_write'] as bool? ?? false,
    canDelete: json['can_delete'] as bool? ?? false,
    createdAt: json['created_at'] as String?,
  );

  Map<String, dynamic> toJson() => {
    'id': id,
    'name': name,
    'path': path,
    'can_write': canWrite,
    'can_delete': canDelete,
  };
}
