import 'package:flutter/material.dart';

import '../models/file_item.dart';

/// 文件/目录列表项
///
/// 对标桌面端 QTreeWidgetItem。
class FileListTile extends StatelessWidget {
  final FileItem item;
  final VoidCallback? onTap;
  final VoidCallback? onLongPress;

  const FileListTile({
    super.key,
    required this.item,
    this.onTap,
    this.onLongPress,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return ListTile(
      leading: Icon(
        item.isDir ? Icons.folder : _fileIcon(item.name),
        color: item.isDir ? Colors.amber.shade700 : theme.colorScheme.primary,
      ),
      title: Text(item.name, overflow: TextOverflow.ellipsis, maxLines: 1),
      subtitle: Row(
        children: [
          if (!item.isDir && item.size > 0) ...[
            Text(item.sizeStr, style: theme.textTheme.bodySmall),
            const SizedBox(width: 12),
          ],
          if (item.modifiedAt > 0)
            Flexible(
              child: Text(
                item.mtimeStr,
                style: theme.textTheme.bodySmall,
                overflow: TextOverflow.ellipsis,
              ),
            ),
        ],
      ),
      onTap: onTap,
      onLongPress: onLongPress,
    );
  }

  IconData _fileIcon(String name) {
    final ext = name.split('.').last.toLowerCase();
    switch (ext) {
      case 'jpg' || 'jpeg' || 'png' || 'gif' || 'webp' || 'bmp' || 'svg':
        return Icons.image;
      case 'mp4' || 'mkv' || 'webm' || 'avi' || 'mov':
        return Icons.videocam;
      case 'mp3' || 'wav' || 'flac' || 'aac' || 'ogg':
        return Icons.audio_file;
      case 'md':
        return Icons.description;
      case 'pdf':
        return Icons.picture_as_pdf;
      case 'zip' || 'rar' || '7z' || 'tar' || 'gz':
        return Icons.folder_zip;
      case 'json' || 'xml' || 'yaml' || 'yml':
        return Icons.code;
      default:
        return Icons.insert_drive_file;
    }
  }
}
