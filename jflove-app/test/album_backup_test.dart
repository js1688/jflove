import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:jflove_app/services/album_backup/album_asset_source.dart';
import 'package:jflove_app/services/album_backup/album_backup_config.dart';
import 'package:jflove_app/services/album_backup/album_backup_service.dart';
import 'package:jflove_app/services/album_backup/album_backup_store.dart';
import 'package:jflove_app/services/album_backup/remote_file_index.dart';
import 'package:jflove_app/services/transfer_service.dart';
import 'package:jflove_app/utils/exception.dart';
import 'package:jflove_app/utils/http_service.dart';
import 'package:jflove_app/utils/session.dart';

/// 相册备份单测（v1.8.2 / F38、F39）
///
/// 设计文档 §3.2 要求：坏资源隔离、视频关闭计数、跨轮游标、远端跳过。
/// 这里通过 `AlbumAssetSource` / `RemoteFileIndex` / `AlbumBackupStore` 的
/// 内存假实现完成，不依赖 photo_manager、sqflite 与真实网络。
void main() {
  late Directory tempDir;
  late File smallFile;
  late File mediumFile;

  setUp(() async {
    tempDir = await Directory.systemTemp.createTemp('album_backup_test_');
    smallFile = File('${tempDir.path}/small.bin')
      ..writeAsBytesSync(List<int>.filled(3, 1));
    mediumFile = File('${tempDir.path}/medium.bin')
      ..writeAsBytesSync(List<int>.filled(4, 2));
  });

  tearDown(() async {
    if (tempDir.existsSync()) await tempDir.delete(recursive: true);
  });

  const baseConfig = AlbumBackupConfig(
    enabled: true,
    diskId: 7,
    remotePath: 'Photos',
    includePhotos: true,
    includeVideos: true,
    wifiOnly: false,
    chargingOnly: false,
  );

  AlbumAsset asset(
    String id, {
    String? title,
    AlbumMediaKind kind = AlbumMediaKind.image,
    DateTime? createdAt,
    Future<File?> Function()? openFile,
  }) => AlbumAsset(
    id: id,
    title: title ?? '$id.jpg',
    kind: kind,
    createDateTime: createdAt ?? DateTime(2026, 10, 1),
    openFile: openFile ?? () async => smallFile,
  );

  ({
    AlbumBackupService service,
    _FakeStore store,
    _FakeAssetSource assets,
    _FakeRemote remote,
    _FakeTransfer transfer,
  })
  build({
    required List<AlbumAsset> assets,
    AlbumBackupConfig config = baseConfig,
    _FakeRemote? remote,
    Map<AlbumCountScope, int>? counts,
  }) {
    final store = _FakeStore();
    final source = _FakeAssetSource(
      assets: assets,
      counts:
          counts ??
          {
            AlbumCountScope.all: assets.length,
            AlbumCountScope.image: assets
                .where((a) => a.kind == AlbumMediaKind.image)
                .length,
            AlbumCountScope.video: assets
                .where((a) => a.kind == AlbumMediaKind.video)
                .length,
          },
    );
    final remoteIndex = remote ?? _FakeRemote();
    final transfer = _FakeTransfer();
    final service = AlbumBackupService(
      transfer,
      store: store,
      configStore: _FakeConfigStore(config),
      assetSource: source,
      remoteIndex: remoteIndex,
      autoStart: false,
    );
    addTearDown(service.dispose);
    return (
      service: service,
      store: store,
      assets: source,
      remote: remoteIndex,
      transfer: transfer,
    );
  }

  test('坏资源隔离：asset.file 抛异常只记失败，其余资源继续上传', () async {
    final env = build(
      assets: [
        asset('good-1'),
        asset('broken', openFile: () async => throw StateError('boom')),
        asset('good-2'),
      ],
    );
    await env.service.init();
    await env.service.scanAndUpload();

    expect(env.transfer.uploads.length, 2, reason: '两个好资源都必须完成提交');
    expect(
      env.transfer.filenames,
      containsAll(<String>['good-1.jpg', 'good-2.jpg']),
    );
    expect(env.store.failureEntries.length, 1, reason: '坏资源记为失败');
    final failure = env.store.failureEntries.first;
    expect(failure.reason, AlbumBackupFailureReason.unavailable);
    expect(failure.reason.contains('/'), isFalse, reason: '失败原因不得含路径');

    final progress = await env.service.emitProgress();
    expect(progress.failed, 1);
    expect(progress.total, 3);
  });

  test('视频开关关闭：视频计入「不在范围」，计数自洽（AC-285/286）', () async {
    final env = build(
      assets: [
        asset('img-1'),
        asset('img-2'),
        asset('video-1', title: 'video-1.mp4', kind: AlbumMediaKind.video),
      ],
      config: baseConfig.copyWith(includeVideos: false),
      counts: const {
        AlbumCountScope.all: 3,
        AlbumCountScope.image: 2,
        AlbumCountScope.video: 1,
      },
    );
    await env.service.init();
    await env.service.scanAndUpload();

    expect(env.transfer.uploads.length, 2, reason: '视频不在范围内，不提交');
    expect(env.transfer.filenames, isNot(contains('video-1.mp4')));

    final progress = await env.service.emitProgress();
    expect(progress.total, 3);
    expect(progress.outOfScope, 1);
    expect(progress.done, 0);
    expect(progress.failed, 0);
    expect(progress.pending, 2);
    expect(
      progress.done + progress.failed + progress.outOfScope + progress.pending,
      progress.total,
      reason: '已备份 + 失败 + 不在范围 + 待备份 = 总数',
    );
  });

  test('全量扫描：2500 张一轮扫完，不再有 2000 上限（AC-287 改造）', () async {
    final assets = List<AlbumAsset>.generate(2500, (i) => asset('asset-$i'));
    final env = build(assets: assets);
    await env.service.init();

    // 预置「已备份」记录，避免 20 条/轮的上传上限干扰扫描观察
    for (final item in assets) {
      env.store.backed[item.id] = AlbumBackupRecord(
        assetId: item.id,
        diskId: baseConfig.diskId,
        remoteRoot: 'Photos',
        remotePath: 'Photos/2026/10/${item.filename}',
        size: 3,
        backedAt: '2026-10-24T00:00:00Z',
      );
    }

    await env.service.scanAndUpload();
    expect(env.assets.visitedIds.length, 2500, reason: '必须全量扫到相册尾部');
    expect(
      env.assets.ranges.map((r) => r.$1),
      contains(2400),
      reason: '最后一页（2400 起）也要访问到',
    );

    env.assets.ranges.clear();
    await env.service.scanAndUpload();
    expect(env.assets.ranges.first.$1, 0, reason: '每轮都从 0 开始全量扫描');
  });

  test('远端同名优先跳过（避免重复流量）；不同名照常上传', () async {
    final remote = _FakeRemote(
      dirs: {
        'Photos/2026/10': [
          const RemoteFileEntry(name: 'a.jpg', size: 3, isDir: false),
          const RemoteFileEntry(name: 'c.jpg', size: 99, isDir: false),
        ],
      },
    );
    final env = build(
      assets: [
        asset('a'),
        asset('b', openFile: () async => mediumFile),
        asset('c'),
      ],
      remote: remote,
    );
    await env.service.init();
    await env.service.scanAndUpload();

    expect(
      env.remote.calls,
      ['Photos', 'Photos/2026', 'Photos/2026/10'],
      reason: '索引构建会从相册根递归遍历到目标月份目录',
    );
    expect(env.transfer.filenames, contains('b.jpg'));
    expect(
      env.transfer.filenames,
      isNot(contains('a.jpg')),
      reason: '远端同名应跳过',
    );
    expect(
      env.transfer.filenames,
      isNot(contains('c.jpg')),
      reason: '远端同名即跳过（不打开本地文件，优先避免重复流量）',
    );
    expect(env.store.backed.keys, containsAll(<String>['a', 'c']));
    expect(env.store.failureEntries, isEmpty);

    final progress = await env.service.emitProgress();
    expect(progress.skippedExisting, 2);
    expect(progress.remoteCheckUnavailable, isFalse);
  });

  test('本地已有新鲜索引：不再拉远端目录，直接按索引跳过', () async {
    final env = build(assets: [asset('a')], remote: _FakeRemote());
    env.store.remoteIndex['Photos/2026/10'] = {'a.jpg': 3};
    env.store.remoteBuiltAt = DateTime.now();
    await env.service.init();
    await env.service.scanAndUpload();

    expect(env.remote.calls, isEmpty, reason: '索引未过期不重复拉目录');
    expect(env.transfer.uploads, isEmpty, reason: '索引命中直接算已上传');
  });

  test('远端删除文件：本地有记录也重新补传（用户裁决）', () async {
    final env = build(assets: [asset('a')], remote: _FakeRemote());
    env.store.remoteIndex['Photos/2026/10'] = const {};
    env.store.remoteBuiltAt = DateTime.now();
    env.store.backed['a'] = AlbumBackupRecord(
      assetId: 'a',
      diskId: baseConfig.diskId,
      remoteRoot: baseConfig.remotePath,
      remotePath: 'Photos/2026/10/a.jpg',
      size: 3,
      backedAt: '2026-10-24T00:00:00Z',
      mediaKind: 'image',
    );
    await env.service.init();
    await env.service.scanAndUpload();

    expect(env.transfer.uploads.length, 1, reason: '远端缺失应自动补传');
    expect(env.transfer.filenames.single, 'a.jpg');
  });

  test('本地标题无扩展名：按远端唯一前缀命中，不重复上传', () async {
    final env = build(
      assets: [asset('vid', title: 'vid', kind: AlbumMediaKind.video)],
      remote: _FakeRemote(),
    );
    env.store.remoteIndex['Photos/2026/10'] = {'vid.mp4': 3};
    env.store.remoteBuiltAt = DateTime.now();
    await env.service.init();
    await env.service.scanAndUpload();

    expect(env.transfer.uploads, isEmpty);
    expect(env.store.backed['vid']!.remotePath, 'Photos/2026/10/vid.mp4');
  });

  test('远端列表失败：暂停该目录上传并提示不可用，不盲传（AC-283 改造）', () async {
    final remote = _FakeRemote(
      dirs: const {'Photos/2026/10': []},
      failDirs: {'Photos/2026/10'},
    );
    final env = build(assets: [asset('a'), asset('b')], remote: remote);
    await env.service.init();
    await env.service.scanAndUpload();

    expect(env.transfer.uploads, isEmpty, reason: '没有远端索引不得盲传');
    expect(env.remote.calls, isNotEmpty, reason: '索引构建会遍历到失败目录');
    expect(env.remote.calls.last, 'Photos/2026/10');
    final progress = await env.service.emitProgress();
    expect(progress.remoteCheckUnavailable, isTrue);
    expect(progress.remoteDeferred, 2);
  });

  test('目标目录首次不存在：按空目录处理，不算远端校验失败（AC-280）', () async {
    final remote = _FakeRemote(
      dirs: const {'Photos/2026/10': []},
      missingDirs: {'Photos/2026/10'},
    );
    final env = build(assets: [asset('a')], remote: remote);
    await env.service.init();
    await env.service.scanAndUpload();

    expect(remote.calls.length, 3, reason: '索引需遍历到目标月份目录');
    expect(env.transfer.uploads.length, 1, reason: '目录不存在应继续上传');
    final progress = await env.service.emitProgress();
    expect(
      progress.remoteCheckUnavailable,
      isFalse,
      reason: '首次备份目录不存在属于正常情况，不应提示校验不可用',
    );
  });

  test('更换目标目录：失败记录清空，历史失败资源重新入队（AC-282）', () async {
    var broken = true;
    final env = build(
      assets: [
        asset(
          'a',
          openFile: () async {
            if (broken) throw StateError('boom');
            return smallFile;
          },
        ),
      ],
      remote: _FakeRemote(),
    );
    await env.service.init();

    for (var i = 0; i < 3; i++) {
      await env.service.scanAndUpload();
    }
    expect(env.store.failureEntries.single.attempts, 3);

    await env.service.setConfig(baseConfig.copyWith(remotePath: 'Backup'));
    expect(env.store.failureEntries, isEmpty, reason: '换目标后旧失败状态不适用');
    broken = false;

    await env.service.scanAndUpload();
    expect(env.transfer.uploads.length, 1);
    expect(env.transfer.remoteDirs.single, 'Backup/2026/10');
  });

  test('更换目标目录：旧记录不生效，历史资源重新入队（AC-282）', () async {
    final env = build(assets: [asset('a')], remote: _FakeRemote());
    await env.service.init();
    env.store.backed['a'] = const AlbumBackupRecord(
      assetId: 'a',
      diskId: 7,
      remoteRoot: 'Photos',
      remotePath: 'Photos/2026/10/a.jpg',
      size: 3,
      backedAt: '2026-10-24T00:00:00Z',
    );

    // 直接切到新目标：旧目标的记录不得命中，历史资源应进入新目标队列
    await env.service.setConfig(baseConfig.copyWith(remotePath: 'Backup'));
    await env.service.scanAndUpload();

    expect(env.transfer.uploads.length, 1, reason: '换目标目录后旧记录不得命中');
    expect(env.transfer.remoteDirs.single, 'Backup/2026/10');
  });

  test('失败自动重试上限 3 次；手动「重试失败项」解锁（AC-288）', () async {
    final env = build(assets: [asset('broken', openFile: () async => null)]);
    await env.service.init();

    for (var i = 0; i < 3; i++) {
      await env.service.scanAndUpload();
    }
    expect(env.store.failureEntries.single.attempts, 3);
    expect(
      env.store.failureEntries.single.reason,
      AlbumBackupFailureReason.unavailable,
    );

    // 第 4 轮不再自动重试（findAsset 不会因该失败项被调用）
    env.assets.findCalls = 0;
    await env.service.scanAndUpload();
    expect(env.assets.findCalls, 0, reason: '达到 3 次后暂停自动重试');

    await env.service.retryFailed();
    expect(env.store.failureEntries.single.attempts, 1, reason: '手动解锁后重新计数');
  });

  test('title 无扩展名时，用本地文件扩展名补全（v1.8.3 / F44）', () async {
    final video = File('${tempDir.path}/original.mp4')
      ..writeAsBytesSync(List<int>.filled(32, 7));
    final env = build(
      assets: [
        asset(
          'vid-1',
          title: 'VID_1',
          kind: AlbumMediaKind.video,
          openFile: () async => video,
        ),
      ],
    );
    await env.service.init();

    await env.service.scanAndUpload();

    expect(env.transfer.filenames, ['VID_1.mp4']);
  });

  test('图片/视频分类统计 + 上次检测时间（v1.8.3）', () async {
    final env = build(
      assets: [asset('img-1'), asset('vid-1', kind: AlbumMediaKind.video)],
      counts: {
        AlbumCountScope.all: 2,
        AlbumCountScope.image: 1,
        AlbumCountScope.video: 1,
      },
    );
    env.store.backed['img-1'] = AlbumBackupRecord(
      assetId: 'img-1',
      diskId: baseConfig.diskId,
      remoteRoot: baseConfig.remotePath,
      remotePath: '${baseConfig.remotePath}/2026/10/img-1.jpg',
      size: 3,
      backedAt: '2026-10-01T00:00:00.000',
      mediaKind: 'image',
    );
    env.store.backed['vid-1'] = AlbumBackupRecord(
      assetId: 'vid-1',
      diskId: baseConfig.diskId,
      remoteRoot: baseConfig.remotePath,
      remotePath: '${baseConfig.remotePath}/2026/10/vid-1.jpg',
      size: 3,
      backedAt: '2026-10-01T00:00:00.000',
      mediaKind: 'video',
    );
    await env.service.init();
    await env.service.scanAndUpload();

    final counts = await env.service.typeCounts();
    expect(counts['imageTotal'], 1);
    expect(counts['videoTotal'], 1);
    expect(counts['imageDone'], 1);
    expect(counts['videoDone'], 1);
    expect(env.service.lastScanAt, isNotNull);
  });

  test('老记录没有媒体类型时，扫描会回填（v1.8.3）', () async {
    final env = build(assets: [asset('old-video', kind: AlbumMediaKind.video)]);
    env.store.backed['old-video'] = AlbumBackupRecord(
      assetId: 'old-video',
      diskId: baseConfig.diskId,
      remoteRoot: baseConfig.remotePath,
      remotePath: '${baseConfig.remotePath}/2026/10/old-video.jpg',
      size: 3,
      backedAt: '2026-10-01T00:00:00.000',
    );
    await env.service.init();
    await env.service.scanAndUpload();

    expect(env.store.backed['old-video']!.mediaKind, 'video');
  });

  test('扫描期间发布 scanning 活动，结束后回到 idle（v1.8.3 / F43）', () async {
    final env = build(
      assets: List.generate(5, (i) => asset('a$i')),
    );
    final phases = <AlbumBackupPhase>[];
    final sub = env.service.activityStream.listen((a) => phases.add(a.phase));
    await env.service.init();

    await env.service.scanAndUpload();
    await sub.cancel();

    expect(phases, contains(AlbumBackupPhase.scanning));
    expect(env.service.activity.phase, AlbumBackupPhase.idle);
    expect(env.service.activity.message, '空闲');
  });

}

// ── 假实现 ──────────────────────────────────────────

class _FakeConfigStore extends AlbumBackupConfigStore {
  _FakeConfigStore(this.value);

  AlbumBackupConfig value;

  @override
  Future<AlbumBackupConfig> load() async => value;

  @override
  Future<void> save(AlbumBackupConfig config) async {
    value = config;
  }
}

class _FakeStore implements AlbumBackupStore {
  final Map<String, AlbumBackupRecord> backed = {};
  final Map<String, AlbumBackupFailure> failures = {};
  final Map<String, Map<String, int>> remoteIndex = {};
  DateTime? remoteBuiltAt;

  int backfillCalls = 0;

  List<AlbumBackupFailure> get failureEntries =>
      failures.values.toList(growable: false);

  @override
  Future<void> backfillRemoteRoot(int diskId, String remoteRoot) async {
    backfillCalls++;
    for (final entry in backed.entries.toList(growable: false)) {
      final record = entry.value;
      if (record.diskId == diskId && record.remoteRoot.isEmpty) {
        backed[entry.key] = AlbumBackupRecord(
          assetId: record.assetId,
          diskId: record.diskId,
          remoteRoot: remoteRoot,
          remotePath: record.remotePath,
          size: record.size,
          backedAt: record.backedAt,
        );
      }
    }
  }

  @override
  Future<Set<String>> backedUpIds({
    required int diskId,
    required String remoteRoot,
  }) async => backed.values
      .where((r) => r.diskId == diskId && r.remoteRoot == remoteRoot)
      .map((r) => r.assetId)
      .toSet();

  @override
  Future<List<AlbumBackupRecord>> recentBackedUp({
    required int diskId,
    required String remoteRoot,
    int limit = 20,
  }) async => const [];

  @override
  Future<Map<String, int>> backedUpCountByKind({
    required int diskId,
    required String remoteRoot,
  }) async {
    final counts = <String, int>{};
    for (final record in backed.values) {
      if (record.diskId != diskId || record.remoteRoot != remoteRoot) continue;
      counts[record.mediaKind] = (counts[record.mediaKind] ?? 0) + 1;
    }
    return counts;
  }

  @override
  Future<Set<String>> backedKindMissingIds({
    required int diskId,
    required String remoteRoot,
  }) async => {
    for (final record in backed.values)
      if (record.diskId == diskId &&
          record.remoteRoot == remoteRoot &&
          record.mediaKind.isEmpty)
        record.assetId,
  };

  @override
  Future<void> setMediaKind(String assetId, String mediaKind) async {
    final record = backed[assetId];
    if (record == null) return;
    backed[assetId] = AlbumBackupRecord(
      assetId: record.assetId,
      diskId: record.diskId,
      remoteRoot: record.remoteRoot,
      remotePath: record.remotePath,
      size: record.size,
      backedAt: record.backedAt,
      mediaKind: mediaKind,
    );
  }

  @override
  Future<String?> getMeta(String key) async => null;

  @override
  Future<void> setMeta(String key, String value) async {}

  @override
  Future<DateTime?> remoteIndexBuiltAt({
    required int diskId,
    required String remoteRoot,
  }) async => remoteBuiltAt;

  @override
  Future<void> replaceRemoteIndex({
    required int diskId,
    required String remoteRoot,
    required Map<String, Map<String, int>> entries,
  }) async {
    remoteIndex
      ..clear()
      ..addAll({
        for (final e in entries.entries) e.key: Map<String, int>.from(e.value),
      });
    remoteBuiltAt = DateTime.now();
  }

  @override
  Future<void> upsertRemoteFile({
    required int diskId,
    required String remoteRoot,
    required String dir,
    required String name,
    required int size,
  }) async {
    remoteIndex.putIfAbsent(dir, () => {})[name] = size;
  }

  @override
  Future<Map<String, Map<String, int>>> loadRemoteIndex({
    required int diskId,
    required String remoteRoot,
  }) async => {
    for (final e in remoteIndex.entries) e.key: Map<String, int>.from(e.value),
  };

  @override
  Future<void> clearRemoteIndex({
    required int diskId,
    required String remoteRoot,
  }) async {
    remoteIndex.clear();
    remoteBuiltAt = null;
  }

  @override
  Future<int> backedUpCount({
    required int diskId,
    required String remoteRoot,
  }) async => backed.values
      .where((r) => r.diskId == diskId && r.remoteRoot == remoteRoot)
      .length;

  @override
  Future<void> markBackedUp(AlbumBackupRecord record) async {
    backed[record.assetId] = record;
    failures.remove(record.assetId);
  }

  @override
  Future<void> markFailed(String assetId, String reason) async {
    final previous = failures[assetId]?.attempts ?? 0;
    failures[assetId] = AlbumBackupFailure(
      assetId: assetId,
      reason: reason,
      attempts: previous + 1,
      triedAt: DateTime.now().toIso8601String(),
    );
  }

  @override
  Future<void> clearFailed(String assetId) async {
    failures.remove(assetId);
  }

  @override
  Future<void> clearAllFailures() async => failures.clear();

  @override
  Future<List<AlbumBackupFailure>> failedEntries() async => failureEntries;

  @override
  Future<int> failedCount() async => failures.length;

  @override
  Future<void> clearAll() async {
    backed.clear();
    failures.clear();
  }

  @override
  Future<void> close() async {}
}

class _FakeAssetSource implements AlbumAssetSource {
  _FakeAssetSource({required this.assets, required this.counts});

  final List<AlbumAsset> assets;
  final Map<AlbumCountScope, int> counts;

  final List<String> visitedIds = [];
  final List<(int, int)> ranges = [];
  int findCalls = 0;

  @override
  Future<bool> ensurePermission() async => true;

  @override
  Future<int> albumCount(AlbumCountScope scope) async => counts[scope] ?? 0;

  @override
  Future<List<AlbumAsset>> listAssets({
    required int start,
    required int end,
  }) async {
    ranges.add((start, end));
    if (start >= assets.length) return const [];
    final stop = end > assets.length ? assets.length : end;
    final page = assets.sublist(start, stop);
    visitedIds.addAll(page.map((item) => item.id));
    return page;
  }

  @override
  Future<AlbumAsset?> findAsset(String id) async {
    findCalls++;
    for (final item in assets) {
      if (item.id == id) return item;
    }
    return null;
  }
}

class _FakeRemote implements RemoteFileIndex {
  _FakeRemote({
    this.dirs = const {},
    this.failDirs = const {},
    this.missingDirs = const {},
  });

  final Map<String, List<RemoteFileEntry>> dirs;
  final Set<String> failDirs;
  final Set<String> missingDirs;
  final List<String> calls = [];

  @override
  Future<List<RemoteFileEntry>> listFiles(int diskId, String path) async {
    calls.add(path);
    if (missingDirs.contains(path)) {
      throw const AppException(code: 404, message: '路径不存在或不是目录');
    }
    if (failDirs.contains(path)) {
      throw AppException.networkError('模拟列表失败');
    }
    final direct = dirs[path];
    if (direct != null) return direct;
    // 由 dirs 的完整路径反推子目录，支持索引构建时的递归遍历
    final children = <String>{};
    final prefix = path.isEmpty ? '' : '$path/';
    for (final key in dirs.keys) {
      if (path.isNotEmpty &&
          key != path &&
          !key.startsWith('$prefix')) {
        continue;
      }
      final rest = key.substring(prefix.length);
      if (rest.isEmpty) continue;
      final segment = rest.split('/').first;
      children.add(segment);
    }
    return [
      for (final child in children)
        RemoteFileEntry(name: child, size: 0, isDir: true),
    ];
  }
}

class _FakeTransfer extends TransferService {
  _FakeTransfer() : super(HttpService(SessionManager()));

  final List<
    ({int diskId, String remoteDir, String filename, int size, String? tag})
  >
  uploads = [];
  int _next = 1;

  List<String> get filenames => uploads.map((u) => u.filename).toList();

  List<String> get remoteDirs => uploads.map((u) => u.remoteDir).toList();

  @override
  Future<String> submitUpload(
    int diskId,
    String remoteDir,
    String localPath,
    String filename,
    int fileSize, {
    String? tag,
  }) async {
    uploads.add((
      diskId: diskId,
      remoteDir: remoteDir,
      filename: filename,
      size: fileSize,
      tag: tag,
    ));
    return 'task-${_next++}';
  }
}
