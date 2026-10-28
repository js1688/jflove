import 'dart:async';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:jflove_app/models/transfer_task.dart';
import 'package:jflove_app/services/transfer_service.dart';
import 'package:jflove_app/utils/file_hash.dart';
import 'package:jflove_app/utils/http_service.dart';
import 'package:jflove_app/utils/session.dart';

/// v1.8.4 / F45：上传两段式管线（校验并发 1、上传并发 3、可取消、带进度）
void main() {
  late Directory dir;

  setUp(() {
    dir = Directory.systemTemp.createTempSync('transfer_service_test_');
  });

  tearDown(() {
    if (dir.existsSync()) dir.deleteSync(recursive: true);
  });

  File writeFile(String name, int bytes) => File('${dir.path}/$name')
    ..writeAsBytesSync(List<int>.generate(bytes, (i) => i % 251));

  Future<void> waitUntil(
    bool Function() condition, {
    String reason = '条件未满足',
    Duration timeout = const Duration(seconds: 10),
  }) async {
    final deadline = DateTime.now().add(timeout);
    while (!condition()) {
      if (DateTime.now().isAfter(deadline)) fail('等待超时：$reason');
      await Future<void>.delayed(const Duration(milliseconds: 5));
    }
  }

  test('上传链路：校验 → init → 分片 → complete，分片数按 1MB 切分', () async {
    final http = _FakeHttp();
    final jobs = <_ManualHashJob>[];
    final service = TransferService(
      http,
      allowHashCache: false,
      hashFactory: (path, onProgress) {
        final job = _ManualHashJob(onProgress);
        jobs.add(job);
        return job;
      },
    );
    addTearDown(service.dispose);

    final file = writeFile('a.bin', 2500 * 1024); // 2.5MB → 3 片
    await service.submitUpload(1, 'Photos/2026/10', file.path, 'a.bin', 2500 * 1024);

    await waitUntil(() => jobs.length == 1, reason: '应进入校验阶段');
    expect(service.tasks.single.status, TaskStatus.hashing);
    jobs.single.complete('hash-a');

    await waitUntil(
      () => service.tasks.single.status == TaskStatus.completed,
      reason: '应完成上传',
    );
    expect(service.tasks.single.percent, 100);
    expect(http.initCalls, 1);
    expect(http.chunkIndices, [0, 1, 2]);
    expect(http.completeCalls, 1);
    expect(http.initFileHash, 'hash-a');
  });

  test('校验进度写回任务状态（不再是无进度黑洞）', () async {
    final http = _FakeHttp();
    final jobs = <_ManualHashJob>[];
    final service = TransferService(
      http,
      allowHashCache: false,
      hashFactory: (path, onProgress) {
        final job = _ManualHashJob(onProgress);
        jobs.add(job);
        return job;
      },
    );
    addTearDown(service.dispose);

    final file = writeFile('big.bin', 100 * 1024);
    await service.submitUpload(1, 'x', file.path, 'big.bin', 100 * 1024);
    await waitUntil(() => jobs.length == 1);

    jobs.single.reportProgress(40 * 1024);
    await waitUntil(() => service.tasks.single.hashPercent == 40);
    expect(service.tasks.single.status, TaskStatus.hashing);
    expect(service.tasks.single.transferred, 40 * 1024);

    jobs.single.complete('h');
    await waitUntil(() => service.tasks.single.status == TaskStatus.completed);
  });

  test('校验并发为 1：大文件逐个校验，其余排队', () async {
    final http = _FakeHttp();
    final jobs = <_ManualHashJob>[];
    final service = TransferService(
      http,
      allowHashCache: false,
      hashFactory: (path, onProgress) {
        final job = _ManualHashJob(onProgress);
        jobs.add(job);
        return job;
      },
    );
    addTearDown(service.dispose);

    for (var i = 0; i < 3; i++) {
      final file = writeFile('f$i.bin', 1024);
      await service.submitUpload(1, 'x', file.path, 'f$i.bin', 1024);
    }

    await waitUntil(() => jobs.length == 1);
    await Future<void>.delayed(const Duration(milliseconds: 50));
    expect(jobs.length, 1, reason: '同一时刻只允许一个文件在算哈希');
    expect(
      service.tasks.where((t) => t.status == TaskStatus.hashing).length,
      1,
    );
    expect(
      service.queuedCount,
      3,
      reason: '一个校验中 + 两个等校验，都属于未开始传输',
    );

    jobs.first.complete('h0');
    await waitUntil(() => jobs.length == 2, reason: '上一个校验完成后才轮到下一个');
  });

  test('取消校验中的任务会立刻中断并释放槽位', () async {
    final http = _FakeHttp();
    final jobs = <_ManualHashJob>[];
    final service = TransferService(
      http,
      allowHashCache: false,
      hashFactory: (path, onProgress) {
        final job = _ManualHashJob(onProgress);
        jobs.add(job);
        return job;
      },
    );
    addTearDown(service.dispose);

    final first = writeFile('first.bin', 1024);
    final second = writeFile('second.bin', 1024);
    final id1 = await service.submitUpload(
      1,
      'x',
      first.path,
      'first.bin',
      1024,
    );
    await service.submitUpload(1, 'x', second.path, 'second.bin', 1024);
    await waitUntil(() => jobs.length == 1);

    service.cancel(id1);

    final cancelled = service.tasks.firstWhere((t) => t.id == id1);
    expect(cancelled.status, TaskStatus.cancelled);
    // 取消后哈希槽位应立即空出来，下一个任务开始校验
    await waitUntil(() => jobs.length == 2, reason: '槽位必须立刻释放');
    expect(http.initCalls, 0, reason: '被取消的任务不得开始上传');
  });

  test('上传并发不超过 maxConcurrent', () async {
    final http = _FakeHttp(chunkDelay: const Duration(milliseconds: 20));
    final service = TransferService(
      http,
      allowHashCache: false,
      hashFactory: (path, onProgress) {
        final job = _ManualHashJob(onProgress);
        scheduleMicrotask(() => job.complete('h-$path'));
        return job;
      },
    );
    addTearDown(service.dispose);

    for (var i = 0; i < 5; i++) {
      final file = writeFile('p$i.bin', 1024);
      await service.submitUpload(1, 'x', file.path, 'p$i.bin', 1024);
    }

    await waitUntil(
      () => service.tasks.every((t) => t.status == TaskStatus.completed),
      reason: '全部任务应完成',
    );
    expect(
      http.maxConcurrentUploads,
      lessThanOrEqualTo(TransferService.maxConcurrent),
    );
    expect(http.maxConcurrentUploads, greaterThan(1), reason: '应确实并行上传');
  });

  test('校验结果命中缓存时不再重算', () async {
    final http = _FakeHttp();
    var hashes = 0;
    final service = TransferService(
      http,
      cache: FileHashCache.at(File('${dir.path}/hash.json')),
      hashFactory: (path, onProgress) {
        hashes++;
        final job = _ManualHashJob(onProgress);
        scheduleMicrotask(() => job.complete('cached-hash'));
        return job;
      },
    );
    addTearDown(service.dispose);

    final file = writeFile('reuse.bin', 4096);
    await service.submitUpload(1, 'x', file.path, 'reuse.bin', 4096);
    await waitUntil(() => service.tasks.first.status == TaskStatus.completed);
    expect(hashes, 1);

    // 同一文件（size + mtime 不变）再次上传：直接复用，不重新校验
    await service.submitUpload(1, 'x', file.path, 'reuse.bin', 4096);
    await waitUntil(() => service.tasks.length == 2);
    await waitUntil(() => service.tasks.last.status == TaskStatus.completed);
    expect(hashes, 1, reason: '缓存命中时不得重算 SHA256');
  });
}

/// 可控的哈希任务（不真起 isolate）
class _ManualHashJob implements HashingJob {
  _ManualHashJob(this._onProgress);

  final HashProgressCallback? _onProgress;
  final Completer<String> _completer = Completer<String>();
  @override
  Future<String> get future => _completer.future;

  void reportProgress(int hashed) => _onProgress?.call(hashed, 0);

  void complete(String hash) {
    if (!_completer.isCompleted) _completer.complete(hash);
  }

  @override
  void cancel() {
    if (!_completer.isCompleted) {
      _completer.completeError(const HashingCancelled());
    }
  }
}

/// 假 HTTP：记录分片顺序，可选注入每片延迟
class _FakeHttp extends HttpService {
  _FakeHttp({this.chunkDelay}) : super(SessionManager());

  final Duration? chunkDelay;
  int initCalls = 0;
  int completeCalls = 0;
  String? initFileHash;
  final List<int> chunkIndices = [];
  int _active = 0;
  int maxConcurrentUploads = 0;

  @override
  Future<Map<String, dynamic>> encryptedPost(
    String path,
    Map<String, dynamic>? data,
  ) async {
    if (path.endsWith('/upload/init')) {
      initCalls++;
      initFileHash = data?['file_hash'] as String?;
      _active++;
      if (_active > maxConcurrentUploads) maxConcurrentUploads = _active;
      return {'upload_id': 'upload-$initCalls'};
    }
    if (path.endsWith('/upload/chunk')) {
      chunkIndices.add((data?['chunk_index'] as num).toInt());
      if (chunkDelay != null) await Future<void>.delayed(chunkDelay!);
      return {};
    }
    if (path.endsWith('/upload/complete')) {
      completeCalls++;
      _active--;
      return {};
    }
    return {};
  }

  @override
  Future<Map<String, dynamic>> encryptedDelete(
    String path,
    Map<String, dynamic>? data,
  ) async => {};
}
