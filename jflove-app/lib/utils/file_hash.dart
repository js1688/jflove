import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:isolate';

import 'package:crypto/crypto.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';

/// 哈希进度回调：已处理字节 / 总字节
typedef HashProgressCallback = void Function(int hashed, int total);

/// 后台哈希任务（可取消、可观测进度）
///
/// 为什么不用 `Isolate.run`：它无法中断，也无法回报进度。
/// 大视频（1GB+）纯 Dart SHA256 需要几十秒到数分钟，用户取消或系统卡顿时
/// 必须能立刻停掉并释放上传队列的并发槽位（v1.8.4 / F45）。
abstract class HashingJob {
  /// 最终 SHA256（十六进制小写）；失败/取消时以异常结束
  Future<String> get future;

  /// 立即中断哈希（幂等）
  void cancel();
}

/// 哈希任务工厂（单测可注入假实现，避免真起 isolate）
typedef HashingJobFactory =
    HashingJob Function(String path, HashProgressCallback? onProgress);

/// 哈希被取消（用户取消任务 / 应用退出）
class HashingCancelled implements Exception {
  const HashingCancelled();

  @override
  String toString() => '文件校验已取消';
}

/// 哈希长时间无进展（文件仅在云端 / 存储不可读 / 进程被冻结）
class HashingStalled implements Exception {
  /// 无进展时长
  final Duration idle;

  const HashingStalled(this.idle);

  @override
  String toString() => '文件校验超时（${idle.inSeconds} 秒无进展）';
}

/// 哈希计算失败（IO 异常等）
class HashingFailed implements Exception {
  final String message;

  const HashingFailed(this.message);

  @override
  String toString() => message;
}

/// 计算文件 SHA256（十六进制小写）
///
/// 纯 CPU/IO 函数，**不要在主 isolate 直接对大文件调用**；
/// 生产路径统一走 [IsolateHashingJob]（带进度、可中断）。
String sha256FileSync(String path) {
  final sink = _DigestSink();
  final input = sha256.startChunkedConversion(sink);
  final file = File(path);
  final raf = file.openSync();
  try {
    while (true) {
      final chunk = raf.readSync(_chunkSize);
      if (chunk.isEmpty) break;
      input.add(chunk);
    }
  } finally {
    raf.closeSync();
  }
  input.close();
  return sink.value.toString();
}

/// 读取块大小（1 MB，与上传分片对齐）
const int _chunkSize = 1024 * 1024;

/// 进度上报步长（16 MB ≈ 40MB/s 下每 0.4 秒一次，够顺滑又不刷爆消息）
const int _progressStep = 16 * 1024 * 1024;

/// 默认「无进展超时」：正常哈希每 16MB 都会回报一次，
/// 90 秒没有任何进展基本可判定读取被卡住（云端占位 / 存储异常）。
const Duration defaultHashStallTimeout = Duration(seconds: 90);

/// 默认实现：`Isolate.spawn` + SendPort 进度 + 可 kill
class IsolateHashingJob implements HashingJob {
  IsolateHashingJob._(this.path, this._onProgress, this._stallTimeout);

  /// 启动一次后台哈希
  factory IsolateHashingJob.start(
    String path, {
    HashProgressCallback? onProgress,
    Duration stallTimeout = defaultHashStallTimeout,
  }) {
    final job = IsolateHashingJob._(path, onProgress, stallTimeout);
    job._start();
    return job;
  }

  /// 目标文件路径
  final String path;
  final HashProgressCallback? _onProgress;
  final Duration _stallTimeout;

  final Completer<String> _completer = Completer<String>();
  ReceivePort? _port;
  Isolate? _isolate;
  Timer? _watchdog;
  bool _cancelled = false;
  bool _finished = false;

  @override
  Future<String> get future => _completer.future;

  Future<void> _start() async {
    final port = ReceivePort();
    _port = port;
    port.listen(_onMessage);
    try {
      _isolate = await Isolate.spawn<_HashRequest>(
        _hashWorkerEntry,
        _HashRequest(
          path: path,
          sendPort: port.sendPort,
          chunkSize: _chunkSize,
          progressStep: _progressStep,
        ),
      );
    } catch (e) {
      _fail(HashingFailed('无法启动校验任务：${e.runtimeType}'));
      return;
    }
    if (_cancelled) {
      _teardown();
      return;
    }
    _resetWatchdog();
  }

  void _onMessage(dynamic message) {
    if (_finished) return;
    if (message is! Map) return;
    switch (message['t']) {
      case 'p':
        final done = (message['d'] as num?)?.toInt() ?? 0;
        final total = (message['n'] as num?)?.toInt() ?? 0;
        _resetWatchdog();
        _onProgress?.call(done, total);
      case 'd':
        _finish((message['h'] as String?) ?? '');
      case 'e':
        _fail(HashingFailed((message['m'] as String?) ?? '校验失败'));
    }
  }

  void _resetWatchdog() {
    _watchdog?.cancel();
    _watchdog = Timer(_stallTimeout, () {
      _fail(HashingStalled(_stallTimeout));
    });
  }

  void _finish(String hash) {
    if (_finished) return;
    _finished = true;
    _teardown();
    if (!_completer.isCompleted) _completer.complete(hash);
  }

  void _fail(Object error) {
    if (_finished) return;
    _finished = true;
    _teardown();
    if (!_completer.isCompleted) _completer.completeError(error);
  }

  @override
  void cancel() {
    if (_finished) return;
    _cancelled = true;
    _finished = true;
    _teardown();
    if (!_completer.isCompleted) {
      _completer.completeError(const HashingCancelled());
    }
  }

  void _teardown() {
    _watchdog?.cancel();
    _watchdog = null;
    // 优先立即杀掉：取消时不能再继续占 CPU（手机尤为敏感）
    _isolate?.kill(priority: Isolate.immediate);
    _isolate = null;
    _port?.close();
    _port = null;
  }
}

/// isolate 入参
class _HashRequest {
  final String path;
  final SendPort sendPort;
  final int chunkSize;
  final int progressStep;

  const _HashRequest({
    required this.path,
    required this.sendPort,
    required this.chunkSize,
    required this.progressStep,
  });
}

/// isolate 内执行的哈希循环（只做 IO + CPU，不碰平台通道）
void _hashWorkerEntry(_HashRequest request) {
  RandomAccessFile? raf;
  try {
    final file = File(request.path);
    final total = file.lengthSync();
    raf = file.openSync();
    final sink = _DigestSink();
    final input = sha256.startChunkedConversion(sink);
    var done = 0;
    var lastReported = 0;
    while (true) {
      final chunk = raf.readSync(request.chunkSize);
      if (chunk.isEmpty) break;
      input.add(chunk);
      done += chunk.length;
      if (done - lastReported >= request.progressStep) {
        lastReported = done;
        request.sendPort.send({'t': 'p', 'd': done, 'n': total});
      }
    }
    input.close();
    request.sendPort.send({'t': 'd', 'h': sink.value.toString(), 'n': total});
  } catch (e) {
    request.sendPort.send({'t': 'e', 'm': '${e.runtimeType}'});
  } finally {
    try {
      raf?.closeSync();
    } catch (_) {
      // 关闭失败不影响已发送的结果
    }
  }
}

/// 收集 `sha256.startChunkedConversion` 的摘要
class _DigestSink implements Sink<Digest> {
  late Digest value;

  @override
  void add(Digest data) => value = data;

  @override
  void close() {}
}

/// 文件哈希缓存（`path + size + mtime` 完全一致才复用）
///
/// 为什么要缓存：上传失败重试（相册备份每轮自动重试 3 次）或用户手动重试时，
/// 重新把 2GB 视频读一遍既慢又毫无意义；桌面端 `file_service._cached_file_hash`
/// 已是同款做法，移动端此前缺失（v1.8.4 / F45）。
class FileHashCache {
  FileHashCache._(this._file, this._entries);

  /// 直接用指定文件构造并**同步加载**已有内容（单测 / 启动时使用）
  factory FileHashCache.at(File file) {
    final entries = <String, _HashCacheEntry>{};
    _loadInto(file, entries);
    return FileHashCache._(file, entries);
  }

  /// 最多缓存的条目数（超出后按最久未写入淘汰）
  static const int maxEntries = 500;

  final File _file;
  final Map<String, _HashCacheEntry> _entries;
  bool _dirty = false;
  Future<void> _writeChain = Future<void>.value();

  /// 打开（并加载）默认缓存文件；平台不可用时返回 null（功能降级，不影响上传）
  static Future<FileHashCache?> open() async {
    try {
      final dir = await getApplicationSupportDirectory();
      return FileHashCache.at(
        File(p.join(dir.path, 'transfer-hash-cache.json')),
      );
    } catch (_) {
      return null;
    }
  }

  /// 查询缓存；未命中或 size/mtime 不一致返回 null
  String? getHash(String path, int size, int mtimeMs) {
    final entry = _entries[_keyOf(path)];
    if (entry == null) return null;
    if (entry.size != size || entry.mtimeMs != mtimeMs) return null;
    return entry.hash;
  }

  /// 写入缓存（内存立即生效，落盘合并写）
  void putHash(String path, int size, int mtimeMs, String hash) {
    if (hash.isEmpty) return;
    _entries[_keyOf(path)] = _HashCacheEntry(
      size: size,
      mtimeMs: mtimeMs,
      hash: hash,
      at: DateTime.now().millisecondsSinceEpoch,
    );
    if (_entries.length > maxEntries) {
      final sorted = _entries.entries.toList()
        ..sort((a, b) => a.value.at.compareTo(b.value.at));
      for (final e in sorted.take(_entries.length - maxEntries)) {
        _entries.remove(e.key);
      }
    }
    _dirty = true;
    unawaited(flush());
  }

  /// 把当前缓存写到磁盘（串行执行，失败静默）
  Future<void> flush() {
    if (!_dirty) return _writeChain;
    _dirty = false;
    final snapshot = <String, dynamic>{
      'v': 1,
      'entries': {
        for (final e in _entries.entries)
          e.key: {
            'size': e.value.size,
            'mtime': e.value.mtimeMs,
            'hash': e.value.hash,
            'at': e.value.at,
          },
      },
    };
    final text = jsonEncode(snapshot);
    _writeChain = _writeChain.then((_) async {
      try {
        await _file.parent.create(recursive: true);
        await _file.writeAsString(text, flush: true);
      } catch (_) {
        // 缓存落盘失败只影响下次重试速度，不影响上传
      }
    });
    return _writeChain;
  }

  /// 从磁盘同步加载（文件不存在 / 损坏时保持为空缓存）
  static void _loadInto(File file, Map<String, _HashCacheEntry> entries) {
    try {
      if (!file.existsSync()) return;
      final raw = jsonDecode(file.readAsStringSync());
      if (raw is! Map) return;
      final rawEntries = raw['entries'];
      if (rawEntries is! Map) return;
      for (final entry in rawEntries.entries) {
        final value = entry.value;
        if (value is! Map) continue;
        final hash = value['hash'] as String? ?? '';
        if (hash.isEmpty) continue;
        entries[entry.key as String] = _HashCacheEntry(
          size: (value['size'] as num?)?.toInt() ?? 0,
          mtimeMs: (value['mtime'] as num?)?.toInt() ?? 0,
          hash: hash,
          at: (value['at'] as num?)?.toInt() ?? 0,
        );
      }
    } catch (_) {
      entries.clear();
    }
  }

  /// 缓存键 = 规范化绝对路径的 SHA256（缓存文件里**不落原始路径**，
  /// 只存「键 + 大小 + mtime + 摘要」，即使被读取也看不出用户目录结构）
  static String _keyOf(String path) =>
      sha256.convert(utf8.encode(p.normalize(p.absolute(path)))).toString();
}

/// 单条缓存
class _HashCacheEntry {
  final int size;
  final int mtimeMs;
  final String hash;
  final int at;

  const _HashCacheEntry({
    required this.size,
    required this.mtimeMs,
    required this.hash,
    required this.at,
  });
}
