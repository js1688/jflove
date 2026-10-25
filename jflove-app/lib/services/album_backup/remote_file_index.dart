import '../../models/file_item.dart';
import '../file_service.dart';

/// 远端目录中的一个条目（只保留去重需要的字段）
class RemoteFileEntry {
  /// 文件名
  final String name;

  /// 文件大小（字节）
  final int size;

  /// 是否目录
  final bool isDir;

  const RemoteFileEntry({
    required this.name,
    required this.size,
    required this.isDir,
  });
}

/// 远端目录索引接口（便于单测注入假列表）
///
/// 生产实现 [FileServiceRemoteFileIndex] 复用
/// `FileService.listFiles` → `HttpService` 加密通道；实现**只读**，
/// 不做任何写入/删除。
abstract class RemoteFileIndex {
  /// 列出 `diskId` 磁盘下 `path`（相对路径）目录内容
  Future<List<RemoteFileEntry>> listFiles(int diskId, String path);
}

/// 基于 [FileService] 的生产实现
class FileServiceRemoteFileIndex implements RemoteFileIndex {
  final FileService _files;

  FileServiceRemoteFileIndex(this._files);

  @override
  Future<List<RemoteFileEntry>> listFiles(int diskId, String path) async {
    final items = await _files.listFiles(diskId, path: path);
    return items.map(_fromFileItem).toList(growable: false);
  }

  RemoteFileEntry _fromFileItem(FileItem item) =>
      RemoteFileEntry(name: item.name, size: item.size, isDir: item.isDir);
}
