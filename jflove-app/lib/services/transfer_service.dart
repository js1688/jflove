import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import '../config/app_config.dart';
import '../models/transfer_task.dart';
import '../utils/exception.dart';
import '../utils/file_hash.dart';
import '../utils/http_service.dart';
import 'transfer_resume_store.dart';

/// 传输任务管理器
///
/// 对标 jflove-desktop/src/utils/transfer_manager.py。
/// 全局单例，管理所有上传/下载任务的进度与状态。
///
/// v1.8.4 / F45：上传拆成「校验段 + 上传段」两段式管线 ——
/// 校验并发 1、上传并发 3，校验结果带进度且可取消，并可跨任务复用缓存。
class TransferService {
  final HttpService _http;
  final HashingJobFactory _hashFactory;
  final TransferResumeStore _resumeStore;
  FileHashCache? _hashCache;
  final bool _enableHashCache;
  Future<FileHashCache?>? _hashCacheFuture;

  final Map<String, TransferTask> _tasks = {};
  final StreamController<List<TransferTask>> _taskController =
      StreamController<List<TransferTask>>.broadcast();
  final Set<String> _cancelledTasks = {};

  /// 等待校验的任务（上传专用；下载直接进 `_readyQueue`）
  final List<_QueuedTask> _pendingQueue = [];

  /// 已校验（或无需校验），等待上传/下载槽位
  final List<_QueuedTask> _readyQueue = [];

  /// 正在执行的哈希任务（任务 ID → 句柄，取消时 kill）
  final Map<String, HashingJob> _hashJobs = {};

  /// 已算好的 SHA256（任务 ID → 摘要），进入上传段时消费
  final Map<String, String> _hashes = {};

  int _activeTransfers = 0;
  int _activeHashes = 0;
  bool _disposed = false;

  /// 上传/下载并发数
  static const int maxConcurrent = 2;

  /// 校验并发数：大文件逐个校验，避免多个大视频同时把手机 CPU 抢满
  static const int maxConcurrentHashes = 1;

  /// 排队上限：调用方（相册备份）据此延后提交，避免传输页无限变长（M-2）
  static const int maxQueueDepth = 10;

  /// 本地最多保留的传输任务记录数（v1.8.3，移动端；只裁剪已结束任务）
  static const int maxTaskHistory = 50;

  /// 校验「无进展」超时：超过即判定读取被卡住（云端占位 / 存储异常）
  static const Duration hashStallTimeout = defaultHashStallTimeout;

  TransferService(
    this._http, {
    HashingJobFactory? hashFactory,
    FileHashCache? cache,
    bool allowHashCache = true,
    TransferResumeStore? resumeStore,
  }) : _hashFactory = hashFactory ?? _defaultHashFactory,
       _hashCache = cache,
       _enableHashCache = allowHashCache,
       _resumeStore = resumeStore ?? TransferResumeStore();

  static HashingJob _defaultHashFactory(
    String path,
    HashProgressCallback? onProgress,
  ) => IsolateHashingJob.start(
    path,
    onProgress: onProgress,
    stallTimeout: hashStallTimeout,
  );

  /// 任务列表流
  Stream<List<TransferTask>> get taskStream => _taskController.stream;

  /// 当前任务列表
  List<TransferTask> get tasks => _tasks.values.toList();

  /// 进行中的任务数
  int get runningCount =>
      _tasks.values.where((t) => t.status == TaskStatus.running).length;

  /// 尚未进入传输（待校验 / 校验中 / 校验完毕等上传槽位）的任务数
  int get queuedCount => _tasks.values
      .where((t) => t.status == TaskStatus.pending || t.status == TaskStatus.hashing)
      .length;

  /// 队列是否已满（相册备份据此把剩余资源留到下一轮，M-2）
  bool get queueSaturated => queuedCount >= maxQueueDepth;

  // ── 提交上传任务 ──

  /// 提交上传任务（通过加密 API 分片上传）
  ///
  /// v1.7.0：新增可选 `tag`（相册备份用 `album:<asset_id>`），并返回任务 ID，
  /// 便于调用方订阅 `taskStream` 关联结果。既有调用方忽略返回值即可。
  Future<String> submitUpload(
    int diskId,
    String remoteDir,
    String localPath,
    String filename,
    int fileSize, {
    String? tag,
  }) async {
    final taskId = _generateId();
    final task = TransferTask(
      id: taskId,
      kind: TaskKind.upload,
      filename: filename,
      localPath: localPath,
      fileSize: fileSize,
      tag: tag,
    );
    _tasks[taskId] = task;
    _notify();
    _pruneTasks();

    _pendingQueue.add(
      _QueuedTask(taskId: taskId, diskId: diskId, remoteDir: remoteDir),
    );
    _pump();
    return taskId;
  }

  /// 上传分片大小（1 MB，与服务端 CHUNK_SIZE 对齐）
  static const int uploadChunkSize = 1024 * 1024;

  /// 校验段：读文件算 SHA256（带进度、可取消、命中缓存直接通过）
  ///
  /// 校验完成后把任务放进 `_readyQueue`，由上传段并行发送；
  /// 因此「下一个文件校验」与「上一个文件上传」可以重叠。
  Future<void> _hashTask(_QueuedTask queued, TransferTask task) async {
    final taskId = queued.taskId;
    try {
      _updateTask(
        taskId,
        status: TaskStatus.hashing,
        hashPercent: 0,
        transferred: 0,
        percent: 0,
      );
      final file = File(task.localPath);
      if (!await file.exists()) {
        _updateTask(taskId, status: TaskStatus.failed, error: '本地文件不存在');
        return;
      }
      final total = task.fileSize > 0 ? task.fileSize : await file.length();

      final cached = await _lookupCachedHash(task.localPath, total);
      final String fileHash;
      if (cached != null) {
        fileHash = cached;
        _updateTask(taskId, hashPercent: 100, transferred: total);
      } else {
        if (_cancelledTasks.contains(taskId)) {
          _updateTask(taskId, status: TaskStatus.cancelled);
          return;
        }
        final job = _hashFactory(task.localPath, (hashed, size) {
          if (_cancelledTasks.contains(taskId)) return;
          final totalBytes = size > 0 ? size : total;
          final pct = totalBytes > 0
              ? ((hashed / totalBytes) * 100).clamp(0, 100).round()
              : 0;
          _updateTask(taskId, transferred: hashed, hashPercent: pct);
        });
        _hashJobs[taskId] = job;
        if (_cancelledTasks.contains(taskId)) job.cancel();
        try {
          fileHash = await job.future;
        } finally {
          _hashJobs.remove(taskId);
        }
        if (fileHash.isNotEmpty) {
          await _storeCachedHash(task.localPath, total, fileHash);
        }
      }
      if (_cancelledTasks.contains(taskId)) {
        _updateTask(taskId, status: TaskStatus.cancelled);
        return;
      }
      _hashes[taskId] = fileHash;
      // 校验完成但还没轮到上传槽位：回到「等待中」，不要一直显示校验中 100%
      _updateTask(
        taskId,
        status: TaskStatus.pending,
        hashPercent: 100,
        transferred: total,
        percent: 0,
      );
      _readyQueue.add(queued);
      _pump();
    } on HashingCancelled {
      _updateTask(taskId, status: TaskStatus.cancelled);
    } on HashingStalled {
      _updateTask(
        taskId,
        status: TaskStatus.failed,
        error: '文件校验超时（${hashStallTimeout.inSeconds} 秒无进展）：'
            '文件可能仅在云端，或存储暂时不可读',
      );
    } on HashingFailed catch (e) {
      _updateTask(taskId, status: TaskStatus.failed, error: '文件校验失败：${e.message}');
    } catch (e) {
      _updateTask(
        taskId,
        status: TaskStatus.failed,
        error: '文件校验失败：${userFacingError(e, fallback: '无法读取文件')}',
      );
    }
  }

  /// 上传段：初始化会话 → 分片上传 → 合并
  Future<void> _doUpload(_QueuedTask queued, TransferTask task) async {
    final taskId = queued.taskId;
    final diskId = queued.diskId;
    final remoteDir = queued.remoteDir;
    String? uploadId;
    final fileHash = _hashes.remove(taskId) ?? '';
    String? resumeKey;
    try {
      final file = File(task.localPath);
      final totalBytes = task.fileSize > 0 ? task.fileSize : await file.length();
      final chunkSize = AppConfig.uploadChunkSize;
      final totalChunks =
          totalBytes > 0 ? ((totalBytes + chunkSize - 1) ~/ chunkSize) : 1;
      resumeKey = TransferResumeStore.uploadKey(
        diskId: diskId,
        remoteDir: remoteDir,
        filename: task.filename,
        size: totalBytes,
        fileHash: fileHash,
      );
      final local = await _resumeStore.loadUpload(resumeKey);

      _updateTask(taskId, status: TaskStatus.running, percent: 0, transferred: 0);
      // v1.9.0：init 带 client_type；服务端按内容指纹返回同一会话与已收分片
      final initResp = await _http.encryptedPost('/api/v1/files/upload/init', {
        'disk_id': diskId,
        'path': remoteDir,
        'filename': task.filename,
        'file_size': totalBytes,
        'total_chunks': totalChunks,
        'file_hash': fileHash,
        'client_type': 'app',
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

      final received = <int>{};
      final rawReceived = initResp['received_chunks'];
      if (rawReceived is List) {
        for (final value in rawReceived) {
          if (value is int) received.add(value);
        }
      } else if (local != null && local['upload_id'] == uploadId) {
        // 服务端未返回已收分片时，本地记录仅作兜底
        final sent = local['sent_chunks'];
        if (sent is List) {
          received.addAll(sent.whereType<int>());
        }
      }
      final expiresAt = (initResp['expires_at'] as num?)?.toDouble() ?? 0.0;
      await _resumeStore.saveUpload(
        resumeKey,
        uploadId: uploadId,
        sentChunks: received,
        fileSize: totalBytes,
        expiresAt: expiresAt,
      );

      final reader = await file.open(mode: FileMode.read);
      try {
        int sent = 0;
        for (int i = 0; i < totalChunks; i++) {
          if (_cancelledTasks.contains(taskId)) {
            await _cleanupServerSession(uploadId);
            await _resumeStore.clearUpload(resumeKey);
            _updateTask(taskId, status: TaskStatus.cancelled);
            return;
          }

          final remaining = totalBytes - sent;
          final readLen = remaining < chunkSize ? remaining : chunkSize;
          final chunk = await reader.read(readLen);
          if (chunk.isEmpty) break;

          if (received.contains(i)) {
            sent += chunk.length;
            final pct =
                totalBytes > 0 ? ((sent / totalBytes) * 100).round() : 0;
            _updateTask(taskId, transferred: sent, percent: pct);
            continue;
          }

          final chunkB64 = base64Encode(chunk);
          // 分片级重试（指数退避，最多 5 次）：单片抖动不推倒整个文件
          for (int attempt = 0; attempt < 5; attempt++) {
            try {
              await _http.encryptedPost('/api/v1/files/upload/chunk', {
                'upload_id': uploadId,
                'chunk_index': i,
                'chunk_data': chunkB64,
              });
              break;
            } catch (e) {
              if (attempt == 4) rethrow;
              await Future<void>.delayed(
                Duration(milliseconds: 300 * (1 << attempt)),
              );
            }
          }

          received.add(i);
          sent += chunk.length;
          if (i % 16 == 0) {
            await _resumeStore.saveUpload(
              resumeKey,
              uploadId: uploadId,
              sentChunks: received,
              fileSize: totalBytes,
              expiresAt: expiresAt,
            );
          }
          final pct = totalBytes > 0 ? ((sent / totalBytes) * 100).round() : 0;
          _updateTask(taskId, transferred: sent, percent: pct);
        }
      } finally {
        await reader.close();
      }

      // ── 异步合并：立即受理 + 轮询 progress ──
      final completeResp = await _http.encryptedPost(
        '/api/v1/files/upload/complete',
        {'upload_id': uploadId, 'async_mode': true},
      );
      // 兼容过渡期服务端/测试桩：complete 无 state 字段即视为旧同步模式已完成
      if (completeResp['state'] == null) {
        await _resumeStore.clearUpload(resumeKey);
        _updateTask(
          taskId,
          status: TaskStatus.completed,
          percent: 100,
          transferred: totalBytes,
        );
        return;
      }
      var state = 'merging';
      final deadline = DateTime.now().add(const Duration(minutes: 30));
      while (state != 'done' &&
          state != 'failed' &&
          DateTime.now().isBefore(deadline)) {
        if (_cancelledTasks.contains(taskId)) {
          await _cleanupServerSession(uploadId);
          await _resumeStore.clearUpload(resumeKey);
          _updateTask(taskId, status: TaskStatus.cancelled);
          return;
        }
        await Future<void>.delayed(const Duration(milliseconds: 2500));
        final progress = await _http.encryptedPost(
          '/api/v1/files/upload/progress',
          {'upload_id': uploadId},
        );
        state = (progress['state'] as String?) ?? 'unknown';
        final processed = (progress['processed'] as num?)?.toInt() ?? 0;
        final size = (progress['file_size'] as num?)?.toInt() ?? totalBytes;
        if (state == 'merging' && size > 0) {
          _updateTask(
            taskId,
            transferred: processed,
            percent: ((processed / size) * 100).round().clamp(0, 100),
          );
        }
        if (state == 'failed') {
          throw AppException(
            code: 500,
            message: (progress['error'] as String?) ?? '服务端合并失败',
          );
        }
        if (state == 'unknown') {
          // 进度记录已清理：用远端大小核验（合并成功但响应丢失场景）
          if (await _remoteHasSize(diskId, remoteDir, task.filename, totalBytes)) {
            state = 'done';
            break;
          }
          throw const AppException(
            code: 500,
            message: '上传状态丢失，请重试',
          );
        }
      }
      if (state != 'done') {
        final done =
            await _remoteHasSize(diskId, remoteDir, task.filename, totalBytes);
        if (!done) {
          throw const AppException(code: 504, message: '服务端合并超时，请稍后重试');
        }
      }

      await _resumeStore.clearUpload(resumeKey);
      _updateTask(
        taskId,
        status: TaskStatus.completed,
        percent: 100,
        transferred: totalBytes,
      );
    } catch (e) {
      if (_cancelledTasks.contains(taskId)) {
        _cancelledTasks.remove(taskId);
        await _cleanupServerSession(uploadId);
        if (resumeKey != null) await _resumeStore.clearUpload(resumeKey);
        _updateTask(taskId, status: TaskStatus.cancelled);
      } else {
        // v1.9.0：网络/合并失败**不删除**服务端会话与已传分片，保留续传
        _updateTask(
          taskId,
          status: TaskStatus.failed,
          error: userFacingError(e, fallback: '上传失败，请稍后重试'),
        );
      }
    }
  }

  /// 取消时清理服务端会话（仅用户主动取消调用）
  Future<void> _cleanupServerSession(String? uploadId) async {
    if (uploadId == null || uploadId.isEmpty) return;
    try {
      await _http.encryptedDelete('/api/v1/files/upload/$uploadId', {});
    } catch (_) {}
  }

  /// 远端目录内是否存在同大小文件（合并完成后的兜底核验）
  Future<bool> _remoteHasSize(
    int diskId,
    String remoteDir,
    String filename,
    int size,
  ) async {
    try {
      final resp = await _http.encryptedGet('/api/v1/files/list', {
        'disk_id': diskId,
        'path': remoteDir,
      });
      final files = resp['files'];
      if (files is List) {
        for (final item in files) {
          if (item is Map &&
              item['name'] == filename &&
              (item['size'] as num?)?.toInt() == size) {
            return true;
          }
        }
      }
    } catch (_) {}
    return false;
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
    _pruneTasks();

    // 下载无需校验，直接进上传/下载槽位队列
    _readyQueue.add(
      _QueuedTask(taskId: taskId, diskId: diskId, remoteDir: remotePath),
    );
    _pump();
  }

  Future<void> _doDownload(_QueuedTask queued, TransferTask task) async {
    final taskId = queued.taskId;
    final diskId = queued.diskId;
    final remotePath = queued.remoteDir;
    final target = File(task.localPath);
    final part = File('${task.localPath}${AppConfig.downloadPartSuffix}');
    final metaFile = File('${task.localPath}${AppConfig.downloadMetaSuffix}');
    try {
      _updateTask(taskId, status: TaskStatus.running);
      await target.parent.create(recursive: true);

      // 本地 part + meta 续传（v1.9.0）
      int start = 0;
      String savedVersion = '';
      if (await part.exists()) {
        start = await part.length();
        if (await metaFile.exists()) {
          try {
            final meta = jsonDecode(await metaFile.readAsString());
            if (meta is Map && meta['version'] is String) {
              savedVersion = meta['version'] as String;
            }
          } catch (_) {}
        }
        if (savedVersion.isEmpty) {
          // 旧版本遗留 part 无版本标识：保守从头下载
          start = 0;
          await _deleteFileQuietly(part);
        }
      }

      EncryptedDownloadResponse? resp;
      for (int attempt = 0; attempt < 2; attempt++) {
        try {
          resp = await _http.encryptedDownloadStreamWithHeaders(
            '/api/v1/files/download',
            {
              'disk_id': diskId,
              'path': remotePath,
              'range_start': start,
            },
          );
        } on AppException catch (e) {
          if (e.code == 416 && start > 0) {
            // 偏移越界（本地 part 大于远端/文件变化）：删除后从头
            await _deleteFileQuietly(part);
            await _deleteFileQuietly(metaFile);
            start = 0;
            savedVersion = '';
            continue;
          }
          rethrow;
        }

        final version = resp.header('X-File-Version');
        if (start > 0 &&
            savedVersion.isNotEmpty &&
            version.isNotEmpty &&
            version != savedVersion) {
          // 远端文件已变化：拼接会坏，删除 part 从头
          await _deleteFileQuietly(part);
          await _deleteFileQuietly(metaFile);
          start = 0;
          savedVersion = '';
          continue;
        }
        final total = int.tryParse(resp.header('X-Stream-Total')) ?? task.fileSize;
        await metaFile.writeAsString(
          jsonEncode({'version': version, 'size': total, 'offset': start}),
        );
        break;
      }
      if (resp == null) {
        throw const AppException(code: 500, message: '下载失败，请重试');
      }

      final expectedTotal =
          int.tryParse(resp.header('X-Stream-Total')) ?? task.fileSize;
      final sink = part.openWrite(
        mode: start > 0 ? FileMode.append : FileMode.write,
      );
      int received = start;
      try {
        await for (final chunk in resp.stream) {
          if (_cancelledTasks.contains(taskId)) {
            _cancelledTasks.remove(taskId);
            await sink.close();
            await _deleteFileQuietly(part);
            await _deleteFileQuietly(metaFile);
            _updateTask(taskId, status: TaskStatus.cancelled);
            return;
          }
          sink.add(chunk);
          received += chunk.length;
          final pct = task.fileSize > 0
              ? ((received / task.fileSize) * 100).round().clamp(0, 100)
              : 0;
          _updateTask(taskId, transferred: received, percent: pct);
        }
        await sink.flush();
      } finally {
        await sink.close();
      }

      if (expectedTotal > 0 && received != expectedTotal) {
        throw const AppException(code: 500, message: '下载数据不完整，请重试');
      }
      // 完成后原子改名到目标文件（同目录内 rename）
      if (await target.exists()) {
        await target.delete();
      }
      await part.rename(task.localPath);
      await _deleteFileQuietly(metaFile);

      _updateTask(
        taskId,
        status: TaskStatus.completed,
        percent: 100,
        transferred: received,
      );
    } catch (e) {
      if (_cancelledTasks.contains(taskId)) {
        _cancelledTasks.remove(taskId);
        await _deleteFileQuietly(part);
        await _deleteFileQuietly(metaFile);
        _updateTask(taskId, status: TaskStatus.cancelled);
      } else {
        // 失败保留 part/meta，供下次自动续传
        _updateTask(
          taskId,
          status: TaskStatus.failed,
          error: userFacingError(e, fallback: '下载失败，请稍后重试'),
        );
      }
    }
  }

  /// 删除本地临时文件（不存在/失败静默）
  Future<void> _deleteFileQuietly(File file) async {
    try {
      if (await file.exists()) await file.delete();
    } catch (_) {}
  }

  // ── 任务调度 ──

  /// 两段式调度：先填满上传/下载槽位，再启动下一个校验
  void _pump() {
    if (_disposed) return;
    while (_activeTransfers < maxConcurrent && _readyQueue.isNotEmpty) {
      final queued = _readyQueue.removeAt(0);
      final task = _tasks[queued.taskId];
      if (task == null) continue;
      _activeTransfers++;
      final future = task.kind == TaskKind.upload
          ? _doUpload(queued, task)
          : _doDownload(queued, task);
      future.whenComplete(() {
        _activeTransfers--;
        _pump();
      });
    }
    while (_activeHashes < maxConcurrentHashes && _pendingQueue.isNotEmpty) {
      final queued = _pendingQueue.removeAt(0);
      final task = _tasks[queued.taskId];
      if (task == null) continue;
      _activeHashes++;
      _hashTask(queued, task).whenComplete(() {
        _activeHashes--;
        _pump();
      });
    }
  }

  // ── 取消任务 ──

  /// 取消任务
  ///
  /// v1.8.4：校验中取消会**立即 kill 哈希 isolate 并释放槽位**；
  /// 旧实现只置标志，哈希仍跑满全程，后续任务一起卡在队列里。
  void cancel(String taskId) {
    final task = _tasks[taskId];
    if (task == null) return;
    if (task.status == TaskStatus.pending ||
        task.status == TaskStatus.running ||
        task.status == TaskStatus.hashing) {
      _cancelledTasks.add(taskId);
      // 排队中的任务直接出队
      _pendingQueue.removeWhere((q) => q.taskId == taskId);
      _readyQueue.removeWhere((q) => q.taskId == taskId);
      _hashes.remove(taskId);
      // 校验中：中断 isolate
      _hashJobs.remove(taskId)?.cancel();
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

  /// 最近一次向 UI 广播的时刻（毫秒）；进度刷新节流到约 5 次/秒
  int _lastNotifyMs = 0;

  void _updateTask(
    String taskId, {
    TaskStatus? status,
    int? transferred,
    int? percent,
    int? hashPercent,
    String? error,
  }) {
    final task = _tasks[taskId];
    if (task == null) return;
    final statusChanged = status != null && status != task.status;
    final errorChanged = error != null && error != task.error;
    final percentChanged =
        (percent != null && percent != task.percent) ||
        (hashPercent != null && hashPercent != task.hashPercent);
    _tasks[taskId] = task.copyWith(
      status: status,
      transferred: transferred,
      percent: percent,
      hashPercent: hashPercent,
      error: error,
    );
    if (statusChanged || errorChanged) {
      _lastNotifyMs = DateTime.now().millisecondsSinceEpoch;
      _notify();
      if (status == TaskStatus.completed ||
          status == TaskStatus.failed ||
          status == TaskStatus.cancelled) {
        _pruneTasks();
      }
      return;
    }
    if (percentChanged) {
      final now = DateTime.now().millisecondsSinceEpoch;
      if (now - _lastNotifyMs >= 200) {
        _lastNotifyMs = now;
        _notify();
      }
    }
  }

  void _notify() {
    if (_disposed || _taskController.isClosed) return;
    _taskController.add(_tasks.values.toList());
  }

  /// 裁剪历史记录：只删除「已结束」的最早任务，运行中/排队中永不删除
  void _pruneTasks() {
    if (_tasks.length <= maxTaskHistory) return;
    final removable = _tasks.entries
        .where(
          (entry) =>
              entry.value.status == TaskStatus.completed ||
              entry.value.status == TaskStatus.failed ||
              entry.value.status == TaskStatus.cancelled,
        )
        .map((entry) => entry.key)
        .toList(growable: false);
    for (final taskId in removable) {
      if (_tasks.length <= maxTaskHistory) break;
      _tasks.remove(taskId);
      _cancelledTasks.remove(taskId);
      _hashes.remove(taskId);
    }
  }

  // ---- 哈希缓存 ----

  Future<FileHashCache?> _ensureHashCache() async {
    final existing = _hashCache;
    if (existing != null) return existing;
    if (!_enableHashCache) return null;
    _hashCacheFuture ??= FileHashCache.open();
    final cache = await _hashCacheFuture;
    _hashCache = cache;
    return cache;
  }

  Future<String?> _lookupCachedHash(String path, int size) async {
    try {
      final cache = await _ensureHashCache();
      if (cache == null) return null;
      final stat = await File(path).stat();
      return cache.getHash(path, size, stat.modified.millisecondsSinceEpoch);
    } catch (_) {
      return null;
    }
  }

  Future<void> _storeCachedHash(String path, int size, String hash) async {
    try {
      final cache = await _ensureHashCache();
      if (cache == null) return;
      final stat = await File(path).stat();
      cache.putHash(path, size, stat.modified.millisecondsSinceEpoch, hash);
    } catch (_) {
      // 缓存写入失败不影响上传
    }
  }

  /// 释放资源：先停调度与哈希，再关闭广播流（避免迟到的回调写已关闭的流）
  void dispose() {
    _disposed = true;
    for (final job in _hashJobs.values) {
      job.cancel();
    }
    _hashJobs.clear();
    _pendingQueue.clear();
    _readyQueue.clear();
    if (!_taskController.isClosed) _taskController.close();
  }
}

/// 排队等待的任务
class _QueuedTask {
  final String taskId;
  final int diskId;

  /// 上传：远端目录；下载：远端文件路径
  final String remoteDir;

  const _QueuedTask({
    required this.taskId,
    required this.diskId,
    required this.remoteDir,
  });
}
