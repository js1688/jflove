import 'dart:typed_data';

import '../models/file_item.dart';
import '../utils/http_service.dart';

/// 文件管理服务
/// 对标 jflove-desktop/src/services/file_service.py
///
/// 服务端接口对照：
///   GET    /api/v1/files/disks      - 可访问磁盘列表
///   GET    /api/v1/files/list       - 列出目录内容
///   POST   /api/v1/files/mkdir      - 创建目录
///   POST   /api/v1/files/rename     - 重命名
///   POST   /api/v1/files/move       - 移动
///   DELETE /api/v1/files            - 删除
///   GET    /api/v1/files/download   - 下载（流式加密帧）
///   POST   /api/v1/files/upload/*   - 分片上传
///   GET    /api/v1/files/preview    - 预览
///   GET    /api/v1/files/stream     - 流式范围读取
class FileService {
  final HttpService _http;

  FileService(this._http);

  /// 列出目录内容
  Future<List<FileItem>> listFiles(int diskId, {String path = '/'}) async {
    final resp = await _http.encryptedGet('/api/v1/files/list', {
      'disk_id': diskId,
      'path': path,
    });
    final items = resp['files'] as List<dynamic>;
    return items
        .map((e) => FileItem.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// 创建目录
  Future<void> createDir(int diskId, String path) async {
    await _http.encryptedPost('/api/v1/files/mkdir', {
      'disk_id': diskId,
      'path': path,
    });
  }

  /// 重命名（服务端用 POST，不是 PUT）
  Future<void> rename(int diskId, String oldPath, String newName) async {
    await _http.encryptedPost('/api/v1/files/rename', {
      'disk_id': diskId,
      'path': oldPath,
      'new_name': newName,
    });
  }

  /// 移动（服务端用 POST，不是 PUT）
  Future<void> move(int diskId, String srcPath, String dstDirPath) async {
    await _http.encryptedPost('/api/v1/files/move', {
      'disk_id': diskId,
      'src_path': srcPath,
      'dst_dir_path': dstDirPath,
    });
  }

  /// 删除
  Future<void> delete(int diskId, String path) async {
    await _http.encryptedDelete('/api/v1/files', {
      'disk_id': diskId,
      'path': path,
    });
  }

  /// 下载文件（服务端用 GET /download）
  Future<Stream<Uint8List>> download(int diskId, String path) async {
    return _http.encryptedDownloadStream('/api/v1/files/download', {
      'disk_id': diskId,
      'path': path,
    });
  }
}
