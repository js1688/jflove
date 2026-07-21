import '../models/virtual_disk.dart';
import '../utils/http_service.dart';

/// 虚拟磁盘管理服务
/// 对标 jflove-desktop/src/services/disk_service.py
class DiskService {
  final HttpService _http;

  DiskService(this._http);

  /// 列出所有虚拟磁盘（管理员专用）
  /// 对应服务端：GET /api/v1/virtual-disks（需 admin 权限）
  Future<List<VirtualDisk>> listDisks() async {
    final resp = await _http.encryptedGet('/api/v1/virtual-disks', null);
    final items = resp['disks'] as List<dynamic>;
    return items
        .map((e) => VirtualDisk.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// 列出当前用户可访问的磁盘列表
  /// 对应服务端：GET /api/v1/files/disks（普通用户可用）
  Future<List<VirtualDisk>> listAccessibleDisks() async {
    final resp = await _http.encryptedGet('/api/v1/files/disks', {});
    final items = resp['disks'] as List<dynamic>;
    return items
        .map((e) => VirtualDisk.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// 创建磁盘
  Future<void> createDisk(String name, String realPath) async {
    await _http.encryptedPost('/api/v1/virtual-disks', {
      'name': name,
      'real_path': realPath,
    });
  }

  /// 更新磁盘
  Future<void> updateDisk(int id, String name, String realPath) async {
    await _http.encryptedPut('/api/v1/virtual-disks/$id', {
      'name': name,
      'real_path': realPath,
    });
  }

  /// 删除磁盘
  Future<void> deleteDisk(int id) async {
    await _http.encryptedDelete('/api/v1/virtual-disks/$id', null);
  }

  /// 浏览虚拟磁盘内指定路径下的子目录
  /// 对应服务端：GET /api/v1/virtual-disks/{disk_id}/browse
  /// [diskId] 虚拟磁盘 ID
  /// [path] 相对路径，空字符串表示根目录
  /// 返回子目录列表，每项含 name（目录名）和 path（相对路径）
  Future<List<Map<String, dynamic>>> browseDirs(
    int diskId, {
    String path = '',
  }) async {
    final resp = await _http.encryptedGet(
      '/api/v1/virtual-disks/$diskId/browse',
      {'path': path},
    );
    final dirs = resp['dirs'] as List<dynamic>;
    return dirs.cast<Map<String, dynamic>>();
  }
}
