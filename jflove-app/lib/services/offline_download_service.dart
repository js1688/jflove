import '../models/offline_task.dart';
import '../utils/http_service.dart';

/// 离线下载服务（移动端**只读**，v1.8.0）
///
/// 对应服务端 `jflove-server/src/controllers/offline_download_controller.py`
/// 的只读接口（设计文档 §2.2）：
///   - `GET /api/v1/offline/tasks`  —— 任务列表（按账号隔离，query 加密信封）
///   - `GET /api/v1/offline/detail` —— 任务详情 + 清单
///   - `GET /api/v1/offline/meta`   —— 初始化元信息（开关 / 可写磁盘 / 策略上限）
///
/// 边界（强制）：
///   - 只实现上述 3 个方法；**不提供** analyze / select / next-page / cancel /
///     retry / delete-record / settings 等写接口（`文档记录/需求文档/v1.8.0.md`
///     F27.5、AC-231）。
///   - 所有 HTTP 一律走 [HttpService]（加密信封），本文件不直接使用 dio、
///     不新增加密代码、不打印 token / session_key。
class OfflineDownloadService {
  OfflineDownloadService(this._http);

  final HttpService _http;

  /// 接口前缀（设计文档 §2.2：统一前缀 `/api/v1/offline`）
  static const String _prefix = '/api/v1/offline';

  /// 任务列表（`GET /tasks`）
  ///
  /// 服务端按 `user_id` 过滤，只返回**当前账号**的任务；`page_size` 上限 100。
  Future<OfflineTaskList> tasks({int page = 1, int pageSize = 20}) async {
    final data = await _http.encryptedGet('$_prefix/tasks', {
      'page': page,
      'page_size': pageSize,
    });
    return OfflineTaskList.fromJson(data);
  }

  /// 任务详情 + 清单（`GET /detail`）
  ///
  /// 他人任务一律 404（不泄露存在性）；`task_id` 走加密 body，不上 URL。
  Future<OfflineTaskDetail> detail(int taskId) async {
    final data = await _http.encryptedGet('$_prefix/detail', {
      'task_id': taskId,
    });
    return OfflineTaskDetail.fromJson(data);
  }

  /// 初始化元信息（`GET /meta`）
  ///
  /// 只含**自己**的默认目录、可写磁盘与全局策略；不含任何服务端绝对路径。
  Future<OfflineMeta> meta() async {
    final data = await _http.encryptedGet('$_prefix/meta', null);
    return OfflineMeta.fromJson(data);
  }
}
