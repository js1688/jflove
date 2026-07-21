import '../utils/http_service.dart';

/// 权限管理服务
/// 对标 jflove-desktop/src/services/permission_service.py
///
/// 服务端接口对照：
///   GET    /api/v1/permissions/users/{user_id}/disks  - 获取用户磁盘权限列表
///   POST   /api/v1/permissions/users/{user_id}/disks/{disk_id} - 设置权限
///   DELETE /api/v1/permissions/users/{user_id}/disks/{disk_id} - 删除权限
class PermissionService {
  final HttpService _http;

  PermissionService(this._http);

  /// 获取指定用户的磁盘权限列表
  /// 对应服务端：GET /api/v1/permissions/users/{userId}/disks
  Future<List<Map<String, dynamic>>> listPermissions(int userId) async {
    final resp = await _http.encryptedGet(
      '/api/v1/permissions/users/$userId/disks',
      {},
    );
    return (resp['permissions'] as List<dynamic>).cast<Map<String, dynamic>>();
  }

  /// 设置用户对指定磁盘的权限
  /// 对应服务端：POST /api/v1/permissions/users/{userId}/disks/{diskId}
  Future<void> setPermission(
    int userId,
    int diskId, {
    bool canRead = false,
    bool canWrite = false,
    bool canDelete = false,
  }) async {
    await _http.encryptedPost(
      '/api/v1/permissions/users/$userId/disks/$diskId',
      {'can_read': canRead, 'can_write': canWrite, 'can_delete': canDelete},
    );
  }

  /// 删除用户对指定磁盘的权限
  /// 对应服务端：DELETE /api/v1/permissions/users/{userId}/disks/{diskId}
  Future<void> deletePermission(int userId, int diskId) async {
    await _http.encryptedDelete(
      '/api/v1/permissions/users/$userId/disks/$diskId',
      null,
    );
  }
}
