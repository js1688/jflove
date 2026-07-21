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
  final TaskStatus status;
  final String? error;

  const TransferTask({
    required this.id,
    required this.kind,
    required this.filename,
    this.localPath = '',
    this.fileSize = 0,
    this.transferred = 0,
    this.percent = 0,
    this.status = TaskStatus.pending,
    this.error,
  });

  TransferTask copyWith({
    String? id,
    TaskKind? kind,
    String? filename,
    String? localPath,
    int? fileSize,
    int? transferred,
    int? percent,
    TaskStatus? status,
    String? error,
  }) => TransferTask(
    id: id ?? this.id,
    kind: kind ?? this.kind,
    filename: filename ?? this.filename,
    localPath: localPath ?? this.localPath,
    fileSize: fileSize ?? this.fileSize,
    transferred: transferred ?? this.transferred,
    percent: percent ?? this.percent,
    status: status ?? this.status,
    error: error ?? this.error,
  );
}
