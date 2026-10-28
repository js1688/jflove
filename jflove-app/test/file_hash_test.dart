import 'dart:async';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:jflove_app/utils/file_hash.dart';

/// v1.8.4 / F45：大文件 SHA256 必须有进度、可中断、结果可缓存
void main() {
  late Directory dir;

  setUp(() {
    dir = Directory.systemTemp.createTempSync('file_hash_test_');
  });

  tearDown(() {
    if (dir.existsSync()) dir.deleteSync(recursive: true);
  });

  File writeFile(String name, int bytes) {
    final file = File('${dir.path}/$name')
      ..writeAsBytesSync(List<int>.generate(bytes, (i) => i % 251));
    return file;
  }

  test('sha256FileSync 与已知向量一致', () {
    final file = File('${dir.path}/abc.bin')..writeAsBytesSync('abc'.codeUnits);

    expect(
      sha256FileSync(file.path),
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    );
  });

  test('后台 isolate 哈希与同步结果一致', () async {
    final file = writeFile('big.bin', 5 * 1024 * 1024);

    final syncHash = sha256FileSync(file.path);
    final isolateHash = await IsolateHashingJob.start(file.path).future;

    expect(isolateHash, syncHash);
    expect(isolateHash.length, 64);
  });

  test('哈希过程回报进度：单调不减且不超过总量，最终 100%', () async {
    // 40MB → 至少跨过一次 16MB 进度上报
    final file = writeFile('progress.bin', 40 * 1024 * 1024);
    final seen = <({int hashed, int total})>[];

    final hash = await IsolateHashingJob.start(
      file.path,
      onProgress: (hashed, total) => seen.add((hashed: hashed, total: total)),
    ).future;

    expect(hash, sha256FileSync(file.path));
    expect(seen, isNotEmpty, reason: '大文件必须回报中间进度');
    var last = 0;
    for (final p in seen) {
      expect(p.total, 40 * 1024 * 1024);
      expect(p.hashed, greaterThanOrEqualTo(last));
      expect(p.hashed, lessThanOrEqualTo(p.total));
      last = p.hashed;
    }
  });

  test('取消哈希立即以 HashingCancelled 结束', () async {
    final file = writeFile('cancel.bin', 80 * 1024 * 1024);
    final started = Completer<void>();
    final job = IsolateHashingJob.start(
      file.path,
      onProgress: (_, _) {
        if (!started.isCompleted) started.complete();
      },
    );

    final expectFuture = expectLater(
      job.future,
      throwsA(isA<HashingCancelled>()),
    );
    await started.future.timeout(const Duration(seconds: 30));
    job.cancel();
    await expectFuture;
  });

  test('无进展超过阈值按 HashingStalled 失败', () async {
    final file = writeFile('stall.bin', 4 * 1024 * 1024);
    final job = IsolateHashingJob.start(
      file.path,
      // 极小阈值：起手就被看门狗判超时（模拟存储卡住）
      stallTimeout: const Duration(milliseconds: 1),
    );

    await expectLater(job.future, throwsA(isA<HashingStalled>()));
  });

  test('缓存按 path + size + mtime 命中，参数不符不命中', () async {
    final file = writeFile('cache.bin', 1024);
    final stat = file.statSync();
    final mtime = stat.modified.millisecondsSinceEpoch;
    final cache = FileHashCache.at(File('${dir.path}/cache.json'));

    expect(cache.getHash(file.path, 1024, mtime), isNull);

    cache.putHash(file.path, 1024, mtime, 'deadbeef');
    expect(cache.getHash(file.path, 1024, mtime), 'deadbeef');
    // 大小不同 / mtime 不同都不命中，避免文件被改写后复用旧值
    expect(cache.getHash(file.path, 2048, mtime), isNull);
    expect(cache.getHash(file.path, 1024, mtime + 1), isNull);

    await cache.flush();
    // 落盘后新实例能直接命中（重试/重启后不再重算）
    final reopened = FileHashCache.at(File('${dir.path}/cache.json'));
    expect(reopened.getHash(file.path, 1024, mtime), 'deadbeef');
  });
}
