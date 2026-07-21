/// 文件/目录项
///
/// 对应桌面端 `_on_files_loaded` 中的字段：name, is_dir, size, modified_at
class FileItem {
  final String name;
  final String path;
  final int size;
  final double modifiedAt; // Unix 时间戳
  final bool isDir;

  const FileItem({
    required this.name,
    required this.path,
    required this.size,
    required this.modifiedAt,
    required this.isDir,
  });

  factory FileItem.fromJson(Map<String, dynamic> json) => FileItem(
    name: json['name'] as String? ?? '',
    path: json['path'] as String? ?? '',
    size: json['size'] as int? ?? 0,
    modifiedAt:
        (json['modified_at'] as num?)?.toDouble() ??
        (json['mtime'] as num?)?.toDouble() ??
        0,
    isDir: json['is_dir'] as bool? ?? false,
  );

  /// 格式化文件大小
  String get sizeStr {
    if (size < 1024) return '$size B';
    if (size < 1024 * 1024) return '${(size / 1024).toStringAsFixed(1)} KB';
    if (size < 1024 * 1024 * 1024) {
      return '${(size / (1024 * 1024)).toStringAsFixed(1)} MB';
    }
    return '${(size / (1024 * 1024 * 1024)).toStringAsFixed(1)} GB';
  }

  /// 格式化修改时间
  String get mtimeStr {
    if (modifiedAt <= 0) return '';
    final dt = DateTime.fromMillisecondsSinceEpoch((modifiedAt * 1000).toInt());
    return '${dt.year}-${dt.month.toString().padLeft(2, '0')}-${dt.day.toString().padLeft(2, '0')} '
        '${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
  }
}
