import 'package:flutter/material.dart';

import '../config/design_tokens.dart';
import '../models/file_item.dart';
import 'app_card.dart';

/// 文件/目录列表项（v1.5.0 接入设计令牌）
///
/// 对标桌面端 `QTreeWidgetItem`、Web 端 `DiskBrowserPage` 的 `.list-row`：
/// 整行是一张统一的 `AppCard`，左侧 36×36 的圆角色块承载类型图标，
/// 色系与 Web 端 `.file-ico-*` 一致（目录=警告橙、文档/代码=品牌、图片=强调红、
/// 音视频=信息蓝、压缩包/其它=中性灰）。
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
    final t = context.tokens;
    final (icon, tone) = _visualOf(item);
    final (iconBg, iconFg) = tone.colors(t);
    final meta = _metaText();

    return AppCard(
      margin: EdgeInsets.only(bottom: t.s2),
      // 内边距交给 InkWell 外层，保证整张卡片都可点击 / 长按
      padding: EdgeInsets.zero,
      child: InkWell(
        onTap: onTap,
        onLongPress: onLongPress,
        child: Padding(
          padding: EdgeInsets.symmetric(horizontal: t.s3, vertical: t.s3),
          child: Row(
            children: [
              Container(
                width: 36,
                height: 36,
                decoration: BoxDecoration(
                  color: iconBg,
                  borderRadius: BorderRadius.circular(t.rMd),
                ),
                child: Icon(icon, size: 18, color: iconFg),
              ),
              SizedBox(width: t.s3),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      item.name,
                      overflow: TextOverflow.ellipsis,
                      maxLines: 1,
                      style: TextStyle(
                        fontSize: 13.5,
                        fontWeight: FontWeight.w500,
                        color: t.fgDefault,
                      ),
                    ),
                    if (meta.isNotEmpty) ...[
                      SizedBox(height: t.s1),
                      Text(
                        meta,
                        overflow: TextOverflow.ellipsis,
                        maxLines: 1,
                        style: TextStyle(fontSize: 11.5, color: t.fgMuted),
                      ),
                    ],
                  ],
                ),
              ),
              if (item.isDir) ...[
                SizedBox(width: t.s2),
                Icon(Icons.chevron_right, size: 16, color: t.fgSubtle),
              ],
            ],
          ),
        ),
      ),
    );
  }

  /// 副信息行：文件大小 + 修改时间（对齐 Web 端 `大小 · 时间` 的拼接方式）
  ///
  /// 目录不显示大小；两者皆无（本地时间为 0）时返回空串、不占位。
  String _metaText() {
    final parts = <String>[
      if (!item.isDir && item.size > 0) item.sizeStr,
      if (item.modifiedAt > 0) item.mtimeStr,
    ];
    return parts.join(' · ');
  }

  /// 扩展名 → 图标 + 色调
  static (IconData, _FileTone) _visualOf(FileItem item) {
    if (item.isDir) return (Icons.folder, _FileTone.folder);
    switch (item.name.split('.').last.toLowerCase()) {
      case 'jpg' || 'jpeg' || 'png' || 'gif' || 'webp' || 'bmp' || 'svg':
        return (Icons.image, _FileTone.img);
      case 'mp4' || 'mkv' || 'webm' || 'avi' || 'mov':
        return (Icons.videocam, _FileTone.video);
      case 'mp3' || 'wav' || 'flac' || 'aac' || 'ogg':
        return (Icons.audio_file, _FileTone.video);
      case 'md':
        return (Icons.description, _FileTone.doc);
      case 'pdf':
        return (Icons.picture_as_pdf, _FileTone.doc);
      case 'json' || 'xml' || 'yaml' || 'yml':
        return (Icons.code, _FileTone.doc);
      case 'zip' || 'rar' || '7z' || 'tar' || 'gz':
        return (Icons.folder_zip, _FileTone.zip);
      default:
        return (Icons.insert_drive_file, _FileTone.zip);
    }
  }
}

/// 文件类型色调（只允许引用设计令牌，不写颜色字面量）
enum _FileTone {
  /// 目录：警告橙
  folder,

  /// 文档 / 代码：品牌色
  doc,

  /// 图片：强调红
  img,

  /// 音视频：信息蓝（Web 端音频与视频共用同一色系）
  video,

  /// 压缩包 / 其它：中性色
  zip;

  /// 返回（底色, 前景色）
  (Color, Color) colors(AppTokens t) {
    switch (this) {
      case _FileTone.folder:
        return (
          AppTokens.warning500.withValues(alpha: 0.14),
          AppTokens.warning500,
        );
      case _FileTone.doc:
        return (t.bgActive, AppTokens.brand600);
      case _FileTone.img:
        return (
          AppTokens.accent500.withValues(alpha: 0.14),
          AppTokens.accent500,
        );
      case _FileTone.video:
        return (AppTokens.info500.withValues(alpha: 0.14), AppTokens.info500);
      case _FileTone.zip:
        return (t.bgSunken, t.fgMuted);
    }
  }
}
