/// 传输任务状态枚举
enum TaskStatus { pending, hashing, running, completed, failed, cancelled }

/// 传输任务方向枚举
enum TaskKind { upload, download }

/// 传输任务模型
///
/// 对标 jflove-desktop/src/utils/transfer_manager.py 的 TransferTask。
class TransferTask {
  final String id;
  final TaskKind kind;
  final String filename;
  final String localPath;
  final int fileSize;
  final int transferred;
  final int percent;

  /// 校验（SHA256）进度 0-100（仅 [TaskStatus.hashing] 阶段有意义，v1.8.4）
  final int hashPercent;
  final TaskStatus status;
  final String? error;

  /// 任务标签（v1.7.0）
  ///
  /// 相册备份用它把传输任务关联回源资源：`album:<asset_id>`；
  /// 普通上传/下载为 null（不影响既有调用方）。
  final String? tag;

  const TransferTask({
    required this.id,
    required this.kind,
    required this.filename,
    this.localPath = '',
    this.fileSize = 0,
    this.transferred = 0,
    this.percent = 0,
    this.hashPercent = 0,
    this.status = TaskStatus.pending,
    this.error,
    this.tag,
  });

  TransferTask copyWith({
    String? id,
    TaskKind? kind,
    String? filename,
    String? localPath,
    int? fileSize,
    int? transferred,
    int? percent,
    int? hashPercent,
    TaskStatus? status,
    String? error,
    String? tag,
  }) => TransferTask(
    id: id ?? this.id,
    kind: kind ?? this.kind,
    filename: filename ?? this.filename,
    localPath: localPath ?? this.localPath,
    fileSize: fileSize ?? this.fileSize,
    transferred: transferred ?? this.transferred,
    percent: percent ?? this.percent,
    hashPercent: hashPercent ?? this.hashPercent,
    status: status ?? this.status,
    error: error ?? this.error,
    tag: tag ?? this.tag,
  );
}
