import '../models/offline_task.dart';
import '../utils/http_service.dart';

/// 离线下载服务（移动端完整功能，v1.8.1）
///
/// 对应服务端 `jflove-server/src/controllers/offline_download_controller.py`
/// （设计文档 v1.8.1 §2.2 / v1.8.0 §2.2）：
///   - `GET  /api/v1/offline/tasks`       任务列表（按账号隔离，query 加密信封）
///   - `GET  /api/v1/offline/detail`      任务详情 + 清单
///   - `GET  /api/v1/offline/meta`        初始化元信息（开关 / 可写磁盘 / 默认目录）
///   - `POST /api/v1/offline/analyze`     创建分析任务
///   - `POST /api/v1/offline/select`      勾选并开始下载
///   - `POST /api/v1/offline/next-page`   图片追加下一页
///   - `POST /api/v1/offline/cancel|retry|delete-record` 任务动作
///   - `POST /api/v1/offline/settings`    保存 / 清除自己的默认目录
///
/// 约束（安全宪法 §9）：
///   - 所有 HTTP 一律走 [HttpService]（加密信封），本文件不直接使用 dio；
///   - `task_id` / `url` / `path` 全部在加密 body 内，不进 URL；
///   - 不打印 token / session_key / 任务内容。
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

  /// 创建分析任务（`POST /analyze`）
  Future<OfflineAnalyzeResult> analyze(String url) async {
    final data = await _http.encryptedPost('$_prefix/analyze', {'url': url});
    return OfflineAnalyzeResult.fromJson(data);
  }

  /// 勾选并开始下载（`POST /select`）
  ///
  /// 服务端会再次校验清单归属、目标目录存在性、磁盘写权限与余量。
  Future<OfflineSelectResult> select({
    required int taskId,
    required List<int> itemIds,
    required int diskId,
    required String targetPath,
    bool packZip = false,
  }) async {
    final data = await _http.encryptedPost('$_prefix/select', {
      'task_id': taskId,
      'item_ids': itemIds,
      'disk_id': diskId,
      'target_path': targetPath,
      'pack_zip': packZip,
    });
    return OfflineSelectResult.fromJson(data);
  }

  /// 图片追加下一页（`POST /next-page`）
  Future<OfflineNextPageResult> nextPage(int taskId) async {
    final data = await _http.encryptedPost('$_prefix/next-page', {
      'task_id': taskId,
    });
    return OfflineNextPageResult.fromJson(data);
  }

  /// 取消任务（`POST /cancel`）
  Future<OfflineTaskAction> cancel(int taskId) async {
    final data = await _http.encryptedPost('$_prefix/cancel', {
      'task_id': taskId,
    });
    return OfflineTaskAction.fromJson(data);
  }

  /// 重试任务（`POST /retry`）
  Future<OfflineTaskAction> retry(int taskId) async {
    final data = await _http.encryptedPost('$_prefix/retry', {
      'task_id': taskId,
    });
    return OfflineTaskAction.fromJson(data);
  }

  /// 删除任务记录（`POST /delete-record`）
  ///
  /// 只软删除记录，**不删除磁盘文件**。
  Future<OfflineTaskAction> deleteRecord(int taskId) async {
    final data = await _http.encryptedPost('$_prefix/delete-record', {
      'task_id': taskId,
    });
    return OfflineTaskAction.fromJson(data);
  }

  /// 保存 / 清除自己的默认目录（`POST /settings`）
  ///
  /// [diskId] 为 0 表示清除；[create] 为 true 时服务端会在目录不存在时创建。
  Future<OfflineSettingsResult> saveSettings({
    required int diskId,
    String relPath = '',
    bool create = false,
  }) async {
    final data = await _http.encryptedPost('$_prefix/settings', {
      'disk_id': diskId,
      'rel_path': relPath,
      'create': create,
    });
    return OfflineSettingsResult.fromJson(data);
  }
}
