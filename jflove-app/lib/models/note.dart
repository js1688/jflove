/// 笔记数据模型
class Note {
  final String? id;
  final String name;
  final String? content;
  final double mtime; // Unix 时间戳

  const Note({this.id, required this.name, this.content, required this.mtime});

  factory Note.fromJson(Map<String, dynamic> json) => Note(
    id: json['id'] as String?,
    name: json['name'] as String? ?? json['filename'] as String? ?? '',
    content: json['content'] as String?,
    mtime:
        (json['mtime'] as num?)?.toDouble() ??
        (json['modified_at'] as num?)?.toDouble() ??
        0,
  );

  /// 格式化修改时间
  String get mtimeStr {
    if (mtime <= 0) return '';
    final dt = DateTime.fromMillisecondsSinceEpoch((mtime * 1000).toInt());
    return '${dt.year}-${dt.month.toString().padLeft(2, '0')}-${dt.day.toString().padLeft(2, '0')} '
        '${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
  }
}
