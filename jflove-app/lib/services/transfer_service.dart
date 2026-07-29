import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

import 'package:pointycastle/export.dart';

import '../models/transfer_task.dart';
import '../utils/http_service.dart';

/// 传输任务管理器
///
/// 对标 jflove-desktop/src/utils/transfer_manager.py。
/// 全局单例，管理所有上传/下载任务的进度与状态。
class TransferService {
  final HttpService _http;
  final Map<String, TransferTask> _tasks = {};
  final StreamController<List<TransferTask>> _taskController =
      StreamController<List<TransferTask>>.broadcast();
  final Set<String> _cancelledTasks = {};
  int _concurrentRunning = 0;
  final List<_QueuedTask> _queue = [];

  /// 最大并发任务数
  static const int maxConcurrent = 3;

  TransferService(this._http);

  /// 任务列表流
  Stream<List<TransferTask>> get taskStream => _taskController.stream;

  /// 当前任务列表
  List<TransferTask> get tasks => _tasks.values.toList();

  /// 进行中的任务数
  int get runningCount =>
      _tasks.values.where((t) => t.status == TaskStatus.running).length;

  // ── 提交上传任务 ──

  /// 提交上传任务（通过加密 API 分片上传）
  Future<void> submitUpload(
    int diskId,
    String remoteDir,
    String localPath,
    String filename,
    int fileSize,
  ) async {
    final taskId = _generateId();
    final task = TransferTask(
      id: taskId,
      kind: TaskKind.upload,
      filename: filename,
      localPath: localPath,
      fileSize: fileSize,
    );
    _tasks[taskId] = task;
    _notify();

    // 排队等待调度
    final queued = _QueuedTask(
      taskId: taskId,
      diskId: diskId,
      remoteDir: remoteDir,
    );
    _queue.add(queued);
    _scheduleNext();
  }

  /// 上传分片大小（1 MB，与服务端 CHUNK_SIZE 对齐）
  static const int uploadChunkSize = 1024 * 1024;

  Future<void> _doUpload(
    String taskId,
    int diskId,
    String remoteDir,
    String localPath,
    String filename,
    int fileSize,
  ) async {
    String? uploadId;
    try {
      _updateTask(taskId, status: TaskStatus.hashing);
      final file = File(localPath);
      if (!await file.exists()) {
        _updateTask(taskId, status: TaskStatus.failed, error: '本地文件不存在');
        return;
      }

      final totalBytes = fileSize > 0 ? fileSize : await file.length();
      final totalChunks = totalBytes > 0
          ? ((totalBytes + uploadChunkSize - 1) ~/ uploadChunkSize)
          : 1;

      // ── 阶段 1：流式计算 SHA256 并回调哈希进度 ──
      final sha256 = SHA256Digest();
      int hashed = 0;
      final raf = await file.open(mode: FileMode.read);
      try {
        while (hashed < totalBytes) {
          if (_cancelledTasks.contains(taskId)) {
            _updateTask(taskId, status: TaskStatus.cancelled);
            return;
          }
          final remaining = totalBytes - hashed;
          final readLen = remaining < uploadChunkSize
              ? remaining
              : uploadChunkSize;
          final chunk = await raf.read(readLen);
          if (chunk.isEmpty) break;
          sha256.update(Uint8List.fromList(chunk), 0, chunk.length);
          hashed += chunk.length;
          final pct = totalBytes > 0
              ? ((hashed / totalBytes) * 100).round()
              : 0;
          _updateTask(taskId, transferred: hashed, percent: pct);
        }
      } finally {
        await raf.close();
      }
      // 从 Digest 中提取最终的 32 字节 SHA256 哈希并转十六进制
      final hashOut = Uint8List(sha256.digestSize);
      sha256.doFinal(hashOut, 0);
      final fileHash = hashOut
          .map((b) => b.toRadixString(16).padLeft(2, '0'))
          .join();

      // ── 阶段 2：初始化上传会话 ──
      _updateTask(
        taskId,
        status: TaskStatus.running,
        percent: 0,
        transferred: 0,
      );
      final initResp = await _http.encryptedPost('/api/v1/files/upload/init', {
        'disk_id': diskId,
        'path': remoteDir,
        'filename': filename,
        'file_size': totalBytes,
        'total_chunks': totalChunks,
        'file_hash': fileHash,
      });
      uploadId = initResp['upload_id'] as String?;
      if (uploadId == null || uploadId.isEmpty) {
        _updateTask(
          taskId,
          status: TaskStatus.failed,
          error: '服务端未返回 upload_id',
        );
        return;
      }

      // ── 阶段 3：分片上传 ──
      final raf2 = await file.open(mode: FileMode.read);
      try {
        int sent = 0;
        for (int i = 0; i < totalChunks; i++) {
          if (_cancelledTasks.contains(taskId)) {
            // 取消时清理服务端临时分片
            try {
              await _http.encryptedDelete('/api/v1/files/upload/$uploadId', {});
            } catch (_) {}
            _updateTask(taskId, status: TaskStatus.cancelled);
            return;
          }

          final remaining = totalBytes - sent;
          final readLen = remaining < uploadChunkSize
              ? remaining
              : uploadChunkSize;
          final chunk = await raf2.read(readLen);
          if (chunk.isEmpty) break;

          final chunkB64 = base64Encode(chunk);
          await _http.encryptedPost('/api/v1/files/upload/chunk', {
            'upload_id': uploadId,
            'chunk_index': i,
            'chunk_data': chunkB64,
          });

          sent += chunk.length;
          final pct = totalBytes > 0 ? ((sent / totalBytes) * 100).round() : 0;
          _updateTask(taskId, transferred: sent, percent: pct);
        }
      } finally {
        await raf2.close();
      }

      // ── 阶段 4：合并分片完成上传 ──
      await _http.encryptedPost('/api/v1/files/upload/complete', {
        'upload_id': uploadId,
      });

      _updateTask(
        taskId,
        status: TaskStatus.completed,
        percent: 100,
        transferred: totalBytes,
      );
    } catch (e) {
      if (_cancelledTasks.contains(taskId)) {
        _cancelledTasks.remove(taskId);
        _updateTask(taskId, status: TaskStatus.cancelled);
      } else {
        // 失败时尝试清理服务端临时分片
        if (uploadId != null) {
          try {
            await _http.encryptedDelete('/api/v1/files/upload/$uploadId', {});
          } catch (_) {}
        }
        _updateTask(taskId, status: TaskStatus.failed, error: e.toString());
      }
    }
  }

  // ── 提交下载任务 ──

  /// 提交下载任务（加密流式下载并写入本地文件，带进度跟踪）
  Future<void> submitDownload(
    int diskId,
    String remotePath,
    String localPath,
    String filename,
    int fileSize,
  ) async {
    final taskId = _generateId();
    final task = TransferTask(
      id: taskId,
      kind: TaskKind.download,
      filename: filename,
      localPath: localPath,
      fileSize: fileSize,
    );
    _tasks[taskId] = task;
    _notify();

    // 排队等待调度
    final queued = _QueuedTask(
      taskId: taskId,
      diskId: diskId,
      remoteDir: remotePath,
    );
    _queue.add(queued);
    _scheduleNext();
  }

  Future<void> _doDownload(
    String taskId,
    int diskId,
    String remotePath,
    String localPath,
    String filename,
    int fileSize,
  ) async {
    try {
      _updateTask(taskId, status: TaskStatus.running);

      // 通过加密流下载
      final stream = await _http.encryptedDownloadStream(
        '/api/v1/files/download',
        {'disk_id': diskId, 'path': remotePath},
      );

      // 写入本地文件
      final file = File(localPath);
      // 确保父目录存在
      await file.parent.create(recursive: true);
      final sink = file.openWrite();
      int received = 0;

      try {
        await for (final chunk in stream) {
          // 检查取消
          if (_cancelledTasks.contains(taskId)) {
            _cancelledTasks.remove(taskId);
            await sink.close();
            // 删除不完整文件
            if (await file.exists()) await file.delete();
            _updateTask(taskId, status: TaskStatus.cancelled);
            return;
          }

          sink.add(chunk);
          received += chunk.length;
          final pct = fileSize > 0 ? ((received / fileSize) * 100).round() : 0;
          _updateTask(taskId, transferred: received, percent: pct);
        }
        await sink.flush();
      } finally {
        await sink.close();
      }

      _updateTask(
        taskId,
        status: TaskStatus.completed,
        percent: 100,
        transferred: received,
      );
    } catch (e) {
      if (!_cancelledTasks.contains(taskId)) {
        _updateTask(taskId, status: TaskStatus.failed, error: e.toString());
      } else {
        _cancelledTasks.remove(taskId);
        _updateTask(taskId, status: TaskStatus.cancelled);
      }
    }
  }

  // ── 任务调度 ──

  void _scheduleNext() {
    while (_concurrentRunning < maxConcurrent && _queue.isNotEmpty) {
      final queued = _queue.removeAt(0);
      final task = _tasks[queued.taskId];
      if (task == null) continue;

      _concurrentRunning++;
      final future = task.kind == TaskKind.upload
          ? _doUpload(
              queued.taskId,
              queued.diskId,
              queued.remoteDir,
              task.localPath,
              task.filename,
              task.fileSize,
            )
          : _doDownload(
              queued.taskId,
              queued.diskId,
              queued.remoteDir,
              task.localPath,
              task.filename,
              task.fileSize,
            );
      future.whenComplete(() {
        _concurrentRunning--;
        _scheduleNext();
      });
    }
  }

  // ── 取消任务 ──

  /// 取消任务
  void cancel(String taskId) {
    final task = _tasks[taskId];
    if (task == null) return;
    if (task.status == TaskStatus.pending ||
        task.status == TaskStatus.running ||
        task.status == TaskStatus.hashing) {
      _cancelledTasks.add(taskId);
      // 如果任务还在队列中，直接移除
      _queue.removeWhere((q) => q.taskId == taskId);
      _updateTask(taskId, status: TaskStatus.cancelled);
    }
  }

  // ── 清除已结束任务 ──

  /// 清除已结束任务，返回清除的任务数量
  int clearFinished() {
    final before = _tasks.length;
    _tasks.removeWhere(
      (_, t) =>
          t.status == TaskStatus.completed ||
          t.status == TaskStatus.failed ||
          t.status == TaskStatus.cancelled,
    );
    final removed = before - _tasks.length;
    if (removed > 0) {
      _notify();
    }
    return removed;
  }

  // ── 任务统计 ──

  /// 任务统计
  Map<String, int> stats() {
    int total = 0, running = 0, completed = 0, failed = 0, cancelled = 0;
    for (final t in _tasks.values) {
      total++;
      switch (t.status) {
        case TaskStatus.running:
        case TaskStatus.hashing:
          running++;
        case TaskStatus.completed:
          completed++;
        case TaskStatus.failed:
          failed++;
        case TaskStatus.cancelled:
          cancelled++;
        case TaskStatus.pending:
          break;
      }
    }
    return {
      'total': total,
      'running': running,
      'completed': completed,
      'failed': failed,
      'cancelled': cancelled,
    };
  }

  // ---- 内部方法 ----

  String _generateId() =>
      '${DateTime.now().millisecondsSinceEpoch}_${Random().nextInt(99999)}';

  void _updateTask(
    String taskId, {
    TaskStatus? status,
    int? transferred,
    int? percent,
    String? error,
  }) {
    final task = _tasks[taskId];
    if (task == null) return;
    _tasks[taskId] = task.copyWith(
      status: status,
      transferred: transferred,
      percent: percent,
      error: error,
    );
    _notify();
  }

  void _notify() {
    _taskController.add(_tasks.values.toList());
  }

  /// 释放资源
  void dispose() {
    _taskController.close();
  }
}

/// 排队等待的任务
class _QueuedTask {
  final String taskId;
  final int diskId;
  final String remoteDir;

  const _QueuedTask({
    required this.taskId,
    required this.diskId,
    required this.remoteDir,
  });
}
