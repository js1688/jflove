import '../utils/http_service.dart';

/// 媒体修复服务（v1.4.2）
///
/// 封装手动离线修复的任务管理接口（与后端 repair_controller 对应）：
///   - create：创建修复任务（健康文件被服务端拒绝：400 无需修复）
///   - list：分页任务列表（全平台共享，所有登录用户可见）
///   - cancel / override / deleteArtifact / deleteRecord
///
/// 说明：服务端对操作类接口统一校验磁盘写+删权限；
/// 只读账号可看列表但操作会 403（UI 层按磁盘权限禁用按钮）。
class RepairService {
  final HttpService _http;

  RepairService(this._http);

  /// 创建媒体修复任务（长按菜单 / 播放失败弹窗「立即修复」）
  /// 对应服务端：POST /api/v1/files/repair/create
  Future<Map<String, dynamic>> createTask(
    int diskId,
    String path,
    String filename,
  ) async {
    return _http.encryptedPost('/api/v1/files/repair/create', {
      'disk_id': diskId,
      'path': path,
      'filename': filename,
    });
  }

  /// 分页获取修复任务列表（全平台共享）
  /// 对应服务端：GET /api/v1/files/repair/tasks
  Future<Map<String, dynamic>> listTasks({
    int page = 1,
    int pageSize = 50,
  }) async {
    return _http.encryptedGet('/api/v1/files/repair/tasks', {
      'page': page,
      'page_size': pageSize,
    });
  }

  /// 取消排队中/执行中的任务（执行中会终止 ffmpeg 并清理半成品）
  Future<Map<String, dynamic>> cancelTask(int taskId) async {
    return _http.encryptedPost('/api/v1/files/repair/cancel', {
      'task_id': taskId,
    });
  }

  /// 覆盖原文件（原损坏文件被直接删除、不留备份，调用前必须二次确认）
  Future<Map<String, dynamic>> overrideOrigin(int taskId) async {
    return _http.encryptedPost('/api/v1/files/repair/override', {
      'task_id': taskId,
    });
  }

  /// 删除修复成功但尚未覆盖的产物
  Future<Map<String, dynamic>> deleteArtifact(int taskId) async {
    return _http.encryptedPost('/api/v1/files/repair/delete-artifact', {
      'task_id': taskId,
    });
  }

  /// 删除终态任务记录（软删除，不影响磁盘产物）
  Future<Map<String, dynamic>> deleteRecord(int taskId) async {
    return _http.encryptedPost('/api/v1/files/repair/delete-record', {
      'task_id': taskId,
    });
  }
}
