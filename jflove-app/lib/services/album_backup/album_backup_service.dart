import 'dart:async';
import 'dart:io';

import 'package:battery_plus/battery_plus.dart';
import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';
import 'package:path/path.dart' as p;

import '../../models/transfer_task.dart';
import '../../utils/exception.dart';
import '../transfer_service.dart';
import 'album_asset_source.dart';
import 'album_backup_config.dart';
import 'album_backup_store.dart';
import 'remote_file_index.dart';

/// 相册备份进度快照（传输页 / 设置页展示用）
class AlbumBackupProgress {
  /// 手机相册总数（拿不到权限时为 0）
  final int total;

  /// 待备份数量（总数 - 已备份 - 失败 - 不在范围；负数归零）
  final int pending;

  /// 正在上传数量
  final int uploading;

  /// 当前「磁盘 + 目标目录」下已备份数量
  final int done;

  /// 失败数量
  final int failed;

  /// 不在范围数量（视频/图片开关关闭的类型）
  final int outOfScope;

  /// 本轮远端已存在并跳过的数量
  final int skippedExisting;

  /// 本轮因远端索引不可用而暂停上传的数量（下轮重试）
  final int remoteDeferred;

  /// 本轮是否存在远端目录列表失败（已暂停该目录上传，不再 fail-open）
  final bool remoteCheckUnavailable;

  const AlbumBackupProgress({
    this.total = 0,
    this.pending = 0,
    this.uploading = 0,
    this.done = 0,
    this.failed = 0,
    this.outOfScope = 0,
    this.skippedExisting = 0,
    this.remoteDeferred = 0,
    this.remoteCheckUnavailable = false,
  });

  static const AlbumBackupProgress empty = AlbumBackupProgress();
}

/// 相册备份当前活动（v1.8.3：页面进度/取消）
enum AlbumBackupPhase {
  /// 空闲
  idle,

  /// 正在核对远端目录
  checkingRemote,

  /// 正在扫描本地相册
  scanning,

  /// 正在上传
  uploading,
}

/// 相册备份活动快照
class AlbumBackupActivity {
  final AlbumBackupPhase phase;

  /// 中文阶段文案
  final String message;

  /// 本轮已处理资源数
  final int processed;

  /// 本轮预计处理数（0 = 未知）
  final int total;

  /// 是否可取消
  final bool cancellable;

  const AlbumBackupActivity({
    this.phase = AlbumBackupPhase.idle,
    this.message = '空闲',
    this.processed = 0,
    this.total = 0,
    this.cancellable = false,
  });

  static const AlbumBackupActivity idle = AlbumBackupActivity();
}

/// 待确认结果的上传任务
class _PendingUpload {
  final String assetId;
  final int diskId;
  final String remoteRoot;
  final String remotePath;
  final String remoteDir;
  final int size;
  final String mediaKind;

  const _PendingUpload({
    required this.assetId,
    required this.diskId,
    required this.remoteRoot,
    required this.remotePath,
    required this.remoteDir,
    required this.size,
    required this.mediaKind,
  });
}

/// 单个资源的处理结果
enum _HandleOutcome {
  /// 已提交上传
  submitted,

  /// 远端已存在同名文件（已补本地记录）
  skippedExisting,

  /// 远端索引不可用：暂停上传，下轮重试（不写失败记录）
  deferred,

  /// 跳过 / 失败（已写失败记录或不在范围）
  skipped,
}

/// 相册自动备份服务（v1.8.2：远端去重 + 扫描健壮性）
///
/// 职责：
///   1. 开关打开后按 60 秒周期扫描新增媒体并提交加密分片上传；
///   2. 用 sqflite 记录已备份资源，保证「不重复上传」；
///   3. 扫描前按 `YYYY/MM` 拉取远端目录列表，同名同大小视为已备份；
///   4. 单资源异常隔离、失败原因可见、失败自动重试上限 3 次；
///   5. 跨轮游标推进，超过 2000 张的相册也会逐轮访问到尾部；
///   6. 订阅 `TransferService.taskStream`，把上传结果回写成记录。
///
/// 安全说明（`AGENTS.md §9.6`）：
///   - 上传全部走 `TransferService`（内部只经 `http_service.dart` 加密通道）；
///   - 远端列表只读当前配置的磁盘，复用既有加密 GET；
///   - 本地记录只存 asset_id / 远端路径 / 大小，不存图片内容与文件名；
///   - 日志与失败原因均不含文件名 / 完整路径。
class AlbumBackupService {
  final TransferService _transfer;
  final AlbumBackupStore _store;
  final AlbumBackupConfigStore _configStore;
  final AlbumAssetSource _assetSource;

  /// 远端目录索引（为 null 时不启用远端去重；生产由 Provider 注入）
  late final RemoteFileIndex? _remoteIndex;

  /// 是否启用周期调度（单测注入 false，避免依赖插件与真实计时器）
  late final bool _autoStart;

  AlbumBackupConfig _config = AlbumBackupConfig.empty;
  Timer? _timer;
  bool _scanning = false;
  final Map<String, _PendingUpload> _pending = {};
  final StreamController<AlbumBackupProgress> _progress =
      StreamController<AlbumBackupProgress>.broadcast();
  final StreamController<AlbumBackupActivity> _activity =
      StreamController<AlbumBackupActivity>.broadcast();
  AlbumBackupActivity _currentActivity = AlbumBackupActivity.idle;
  AlbumBackupProgress _currentProgress = AlbumBackupProgress.empty;
  int _lastActivityAt = 0;
  bool _cancelRequested = false;

  /// 上次检测完成时间（本地持久化）
  DateTime? _lastScanAt;

  /// 上次检测新上传数量
  int _lastScanUploaded = 0;

  /// 上次检测远端已存在跳过数量
  int _lastScanSkipped = 0;

  /// 本轮提交上传数量（用于上次检测摘要）
  int _lastSubmitted = 0;

  /// 自动检测频率（秒）
  static const int scanIntervalSeconds = 60;

  /// 上次检测信息（相册页展示，避免黑匣子）
  DateTime? get lastScanAt => _lastScanAt;
  int get lastScanUploaded => _lastScanUploaded;
  int get lastScanSkipped => _lastScanSkipped;

  /// 远端索引建立时间（相册页展示）
  DateTime? get remoteIndexBuiltAt => _remoteIndexBuiltAt;
  StreamSubscription<List<TransferTask>>? _taskSub;
  StreamSubscription<List<ConnectivityResult>>? _connectivitySub;

  /// 刚完成但可能还没写入 sqflite 的资源（issue #6：防扫描窗口内重复提交）
  final Set<String> _recentlyDone = {};

  /// 跨轮游标：下一轮从该偏移继续（扫到尾部回绕 0）

  /// 本地持久化的远端索引（`dir -> {name: size}`），建立后跨轮复用
  Map<String, Map<String, int>> _remoteIndexData = {};

  /// 索引建立时间（null = 尚未建立）
  DateTime? _remoteIndexBuiltAt;

  /// 索引是否已完整建立（未建立完成时不允许上传）
  bool _remoteIndexReady = false;

  /// 是否正在建立索引（同一时刻只允许一个）
  bool _indexBuilding = false;

  /// 索引有效期：超过后自动重建（手动「立即检查/全量核对」强制重建）
  ///
  /// 10 分钟是为了兼顾「远端删除后能自动补传」与「不频繁拉目录」；
  /// 手动核对可立即刷新。
  static const Duration remoteIndexTtl = Duration(minutes: 10);

  /// 本轮因远端索引不可用而暂停上传的数量
  int _lastDeferred = 0;

  /// 本轮远端列表失败的目录数（>0 时界面提示「远端校验暂不可用」）
  int _remoteCheckFailed = 0;

  /// 本轮「远端已存在跳过」计数
  int _lastSkippedExisting = 0;

  /// 单轮最多提交的上传任务数（避免一次塞爆传输队列）
  static const int _maxSubmitPerRound = 20;

  /// 分页大小（photo_manager 单次查询条数）
  static const int _pageSize = 200;

  /// 单个失败项自动重试上限
  static const int _maxAutoRetries = 3;

  AlbumBackupService(
    this._transfer, {
    AlbumBackupStore? store,
    AlbumBackupConfigStore? configStore,
    AlbumAssetSource? assetSource,
    RemoteFileIndex? remoteIndex,
    bool autoStart = true,
  }) : _store = store ?? SqfliteAlbumBackupStore(),
       _configStore = configStore ?? AlbumBackupConfigStore(),
       _assetSource = assetSource ?? PhotoManagerAlbumAssetSource() {
    _remoteIndex = remoteIndex;
    _autoStart = autoStart;
    _taskSub = _transfer.taskStream.listen(_onTasks);
  }

  /// 当前配置
  AlbumBackupConfig get config => _config;

  /// 进度流
  Stream<AlbumBackupProgress> get progressStream => _progress.stream;

  /// 活动流（阶段/进度/是否可取消）
  Stream<AlbumBackupActivity> get activityStream => _activity.stream;

  /// 当前活动快照
  AlbumBackupActivity get activity => _currentActivity;

  /// 最近一次进度快照
  AlbumBackupProgress get progress => _currentProgress;

  /// 当前归一化后的远端目标根目录
  String get _remoteRoot => _normalizeRoot(_config.remotePath);

  static String _normalizeRoot(String path) =>
      path.replaceAll(RegExp(r'^/+|/+$'), '');

  /// 初始化：读本地配置，开关已打开则启动周期扫描
  Future<void> init() async {
    _config = await _configStore.load();
    if (_config.isConfigured) {
      // 本地库 v1 → v2：旧记录（remote_root='') 回填当前目标，升级不全量重传
      await _store.backfillRemoteRoot(_config.diskId, _remoteRoot);
    }
    await _loadLastScan();
    await _loadRemoteIndex();
    if (_autoStart &&
        _config.enabled &&
        _config.isConfigured &&
        _config.hasMediaType) {
      _startTimer();
      _watchConnectivity();
      // v1.8.3：首帧后再扫描，避免启动即被大相册/大视频阻塞
      unawaited(Future<void>.delayed(const Duration(seconds: 2), scanAndUpload));
    }
    await emitProgress();
  }

  /// 保存配置；开关打开时立即启动扫描，关闭时停止调度
  Future<void> setConfig(AlbumBackupConfig config) async {
    final targetChanged =
        config.diskId != _config.diskId ||
        _normalizeRoot(config.remotePath) != _remoteRoot;

    _config = config;
    await _configStore.save(config);

    if (targetChanged) {
      // 换磁盘/目标目录后，旧目标下的失败状态与远端索引不再适用：
      // 清空失败记录、重建索引，保证历史资源进入新目标的待传队列（F38.2 / AC-282）。
      await _store.clearAllFailures();
      _remoteIndexData = {};
      _remoteIndexReady = false;
      _remoteIndexBuiltAt = null;
    }

    if (_autoStart && config.enabled && config.isConfigured) {
      _startTimer();
      _watchConnectivity();
      unawaited(scanAndUpload());
    } else {
      _stopTimer();
      _stopConnectivity();
    }
    await emitProgress();
  }

  /// 请求相册读取权限（Android 13+ 分媒体类型）
  Future<bool> ensurePermission() => _assetSource.ensurePermission();

  /// 读取一次当前进度（供页面首次渲染）
  Future<AlbumBackupProgress> emitProgress() async {
    final total = await _assetSource.albumCount(AlbumCountScope.all);

    // 「不在范围」= 被开关关闭的媒体类型数量（视频/图片）
    var outOfScope = 0;
    if (!_config.includePhotos) {
      outOfScope += await _assetSource.albumCount(AlbumCountScope.image);
    }
    if (!_config.includeVideos) {
      outOfScope += await _assetSource.albumCount(AlbumCountScope.video);
    }

    final done = _config.isConfigured
        ? await _store.backedUpCount(
            diskId: _config.diskId,
            remoteRoot: _remoteRoot,
          )
        : 0;
    final failed = await _store.failedCount();
    final remaining = total - done - failed - outOfScope;
    final pending = remaining < 0 ? 0 : remaining;

    final snapshot = AlbumBackupProgress(
      total: total,
      pending: pending,
      uploading: _pending.length,
      done: done,
      failed: failed,
      outOfScope: outOfScope,
      skippedExisting: _lastSkippedExisting,
      remoteDeferred: _lastDeferred,
      remoteCheckUnavailable: _remoteCheckFailed > 0,
    );
    _currentProgress = snapshot;
    if (!_progress.isClosed) _progress.add(snapshot);
    return snapshot;
  }

  /// 立即扫描并提交上传（幂等：已在处理中则直接返回）
  ///
  /// [manual] 为 true 时（「立即检查」）清空失败记录，等价于手动解锁自动重试。
  Future<void> scanAndUpload({
    bool manual = false,
    bool forceIndex = false,
  }) async {
    if (_scanning || !_config.enabled || !_config.isConfigured) return;
    if (!_config.hasMediaType) return;
    if (!await _conditionsMet()) return;

    _scanning = true;
    _cancelRequested = false;
    _lastSubmitted = 0;
    _remoteCheckFailed = 0;
    _lastSkippedExisting = 0;
    _lastDeferred = 0;
    _emitActivity(
      phase: _remoteIndex == null
          ? AlbumBackupPhase.scanning
          : AlbumBackupPhase.checkingRemote,
      message: _remoteIndex == null ? '正在扫描相册' : '正在核对远端索引',
      processed: 0,
      total: 0,
      cancellable: true,
      force: true,
    );
    try {
      if (!await _assetSource.ensurePermission()) return;
      if (_cancelRequested) return;
      if (manual) await _store.clearAllFailures();

      // 先建索引，再上传：索引未完整建立时绝不盲传
      if (!await _ensureRemoteIndex(force: forceIndex || manual)) {
        final total = await _assetSource.albumCount(AlbumCountScope.all);
        _lastDeferred = total;
        _emitActivity(
          phase: AlbumBackupPhase.idle,
          message: '远端索引不可用，已暂停上传（下轮重试）',
          processed: 0,
          total: 0,
          cancellable: false,
          force: true,
        );
        return;
      }

      final diskId = _config.diskId;
      final remoteRoot = _remoteRoot;
      final backed = await _store.backedUpIds(
        diskId: diskId,
        remoteRoot: remoteRoot,
      );
      // 老记录（v1/v2 无媒体类型）在扫描时顺手回填，用于图片/视频分类统计
      final missingKinds = await _store.backedKindMissingIds(
        diskId: diskId,
        remoteRoot: remoteRoot,
      );
      final failures = await _store.failedEntries();
      final failureById = {for (final f in failures) f.assetId: f};
      final pendingIds = _pending.values.map((item) => item.assetId).toSet();
      final attempted = <String>{};
      var submitted = 0;
      final totalCount = await _assetSource.albumCount(AlbumCountScope.all);
      _emitActivity(
        phase: AlbumBackupPhase.scanning,
        message: '正在扫描并上传',
        processed: 0,
        total: totalCount,
        cancellable: true,
        force: true,
      );

      // ① 失败项自动重试：每轮最多 1 次；累计 3 次后暂停（手动可解锁）
      for (final failure in failures) {
        if (_cancelRequested || submitted >= _maxSubmitPerRound) break;
        if (failure.attempts >= _maxAutoRetries) continue;
        if (backed.contains(failure.assetId) ||
            pendingIds.contains(failure.assetId)) {
          continue;
        }
        attempted.add(failure.assetId);
        AlbumAsset? asset;
        try {
          asset = await _assetSource.findAsset(failure.assetId);
        } catch (_) {
          await _markFailed(
            failure.assetId,
            AlbumBackupFailureReason.readFailed,
          );
          continue;
        }
        if (asset == null) {
          await _markFailed(
            failure.assetId,
            AlbumBackupFailureReason.unavailable,
          );
          continue;
        }
        try {
          final outcome = await _handleAsset(
              asset,
              diskId,
              remoteRoot,
              backed: backed,
            );
          if (outcome == _HandleOutcome.submitted) {
            submitted++;
            _lastSubmitted = submitted;
            pendingIds.add(asset.id);
          } else if (outcome == _HandleOutcome.skippedExisting) {
            backed.add(asset.id);
          } else if (outcome == _HandleOutcome.deferred) {
            _lastDeferred++;
          }
        } catch (_) {
          await _markFailed(asset.id, AlbumBackupFailureReason.readFailed);
        }
      }

      // ② 全量扫描：每轮从 0 扫到相册尾部（不再有 2000 上限）；
      //    提交仍按 `_maxSubmitPerRound` 限流，未提交的下一轮继续。
      var pageStart = 0;
      var visited = 0;
      while (!_cancelRequested) {
        final assets = await _assetSource.listAssets(
          start: pageStart,
          end: pageStart + _pageSize,
        );
        if (assets.isEmpty) {
          if (pageStart == 0) {
                  break;
          }
          // 相册尾部：回绕到头部继续（坏资源/新资源不会永久饿死）
          pageStart = 0;
              continue;
        }

        for (var i = 0; i < assets.length; i++) {
          if (_cancelRequested) break;
          final asset = assets[i];
          visited++;
          _emitActivity(
            processed: visited,
            total: totalCount,
            message: submitted > 0 ? '正在扫描并上传' : '正在扫描',
          );

          // 老记录回填媒体类型（无论远端索引是否启用）
          if (missingKinds.remove(asset.id)) {
            await _store.setMediaKind(
              asset.id,
              asset.kind == AlbumMediaKind.video ? 'video' : 'image',
            );
          }
          if (pendingIds.contains(asset.id) ||
              _recentlyDone.contains(asset.id)) {
            continue;
          }
          if (backed.contains(asset.id) && _remoteIndex == null) {
            continue;
          }
          final priorFailure = failureById[asset.id];
          if (priorFailure != null &&
              (priorFailure.attempts >= _maxAutoRetries ||
                  attempted.contains(asset.id))) {
            continue;
          }
          if (!_matchesType(asset)) continue;
          // 已提交 20 项也继续扫描（保证全量核对），只是本轮不再提交
          if (submitted >= _maxSubmitPerRound) continue;

          // 单个资源独立 try/catch：任何异常都不得中止整轮
          try {
            final outcome = await _handleAsset(
              asset,
              diskId,
              remoteRoot,
              backed: backed,
            );
            if (outcome == _HandleOutcome.submitted) {
              submitted++;
              pendingIds.add(asset.id);
            } else if (outcome == _HandleOutcome.skippedExisting) {
              backed.add(asset.id);
            } else if (outcome == _HandleOutcome.deferred) {
              _lastDeferred++;
            }
          } catch (_) {
            await _markFailed(asset.id, AlbumBackupFailureReason.readFailed);
          }
        }

        if (assets.length < _pageSize) break;
        pageStart += assets.length;
      }
    } catch (e) {
      // 扫描级失败（权限/平台异常）不静默：写日志与失败计数，不输出文件名
      debugPrint('[相册备份] 扫描失败: ${e.runtimeType}');
    } finally {
      _scanning = false;
      if (!_cancelRequested) {
        await _saveLastScan();
      }
      _cancelRequested = false;
      _emitActivity(
        phase: AlbumBackupPhase.idle,
        message: '空闲',
        processed: 0,
        total: 0,
        cancellable: false,
        force: true,
      );
      await emitProgress();
    }
  }

  /// 「立即检查」：手动解锁失败项后立刻扫描一次
  Future<void> checkNow() => scanAndUpload(manual: true, forceIndex: true);

  /// 「重新核对远端」：清空本轮缓存并重新扫描（不清失败记录）
  ///
  /// v1.8.3：不 await 整轮扫描，页面通过 `activityStream` 展示进度；
  /// 用户可随时 `cancelScan()`。
  Future<void> recheckRemote() async {
    _remoteIndexData = {};
    _remoteIndexReady = false;
    _remoteIndexBuiltAt = null;
    await scanAndUpload(forceIndex: true);
  }

  /// 停止本轮核对/扫描（不取消已提交的传输任务）
  void cancelScan() {
    _cancelRequested = true;
    _emitActivity(force: true, message: '正在停止…', cancellable: false);
  }

  /// 单条失败重试
  Future<void> retryOne(String assetId) async {
    await _store.clearFailed(assetId);
    unawaited(scanAndUpload());
  }

  /// 按媒体类型统计：图片/视频的总数与已同步数（v1.8.3）
  Future<Map<String, int>> typeCounts() async {
    final images = await _assetSource.albumCount(AlbumCountScope.image);
    final videos = await _assetSource.albumCount(AlbumCountScope.video);
    var imageDone = 0;
    var videoDone = 0;
    if (_config.isConfigured) {
      final done = await _store.backedUpCountByKind(
        diskId: _config.diskId,
        remoteRoot: _remoteRoot,
      );
      imageDone = done['image'] ?? 0;
      videoDone = done['video'] ?? 0;
    }
    return {
      'imageTotal': images,
      'imageDone': imageDone,
      'videoTotal': videos,
      'videoDone': videoDone,
    };
  }

  Future<void> _loadRemoteIndex() async {
    if (_remoteIndex == null || !_config.isConfigured) return;
    try {
      _remoteIndexData = await _store.loadRemoteIndex(
        diskId: _config.diskId,
        remoteRoot: _remoteRoot,
      );
      _remoteIndexBuiltAt = await _store.remoteIndexBuiltAt(
        diskId: _config.diskId,
        remoteRoot: _remoteRoot,
      );
      _remoteIndexReady = _remoteIndexBuiltAt != null;
    } catch (_) {
      _remoteIndexReady = false;
    }
  }

  Future<void> _loadLastScan() async {
    try {
      final at = await _store.getMeta('last_scan_at');
      _lastScanAt = at == null ? null : DateTime.tryParse(at);
      _lastScanUploaded =
          int.tryParse(await _store.getMeta('last_scan_uploaded') ?? '') ?? 0;
      _lastScanSkipped =
          int.tryParse(await _store.getMeta('last_scan_skipped') ?? '') ?? 0;
    } catch (_) {
      // 元信息读取失败不影响主流程
    }
  }

  Future<void> _saveLastScan() async {
    _lastScanAt = DateTime.now();
    _lastScanUploaded = _lastSubmitted;
    _lastScanSkipped = _lastSkippedExisting;
    try {
      await _store.setMeta('last_scan_at', _lastScanAt!.toIso8601String());
      await _store.setMeta('last_scan_uploaded', '$_lastScanUploaded');
      await _store.setMeta('last_scan_skipped', '$_lastScanSkipped');
    } catch (_) {
      // 持久化失败只影响展示
    }
  }

  /// 最近上传（只读本地记录）
  Future<List<AlbumBackupRecord>> recentUploads({int limit = 20}) {
    if (!_config.isConfigured) return Future.value(const []);
    return _store.recentBackedUp(
      diskId: _config.diskId,
      remoteRoot: _remoteRoot,
      limit: limit,
    );
  }

  /// 发布活动快照；`force=false` 时按 250ms 节流
  void _emitActivity({
    AlbumBackupPhase? phase,
    String? message,
    int? processed,
    int? total,
    bool? cancellable,
    bool force = false,
  }) {
    final next = AlbumBackupActivity(
      phase: phase ?? _currentActivity.phase,
      message: message ?? _currentActivity.message,
      processed: processed ?? _currentActivity.processed,
      total: total ?? _currentActivity.total,
      cancellable: cancellable ?? _currentActivity.cancellable,
    );
    final now = DateTime.now().millisecondsSinceEpoch;
    if (!force && now - _lastActivityAt < 250) return;
    _lastActivityAt = now;
    _currentActivity = next;
    if (!_activity.isClosed) _activity.add(next);
  }

  /// 重试全部失败项（先清失败标记解除 3 次上限，再触发一次扫描）
  Future<void> retryFailed() async {
    await _store.clearAllFailures();
    await emitProgress();
    await scanAndUpload(manual: true);
  }

  /// 失败记录（设置页「查看原因」）
  Future<List<AlbumBackupFailure>> failedEntries() => _store.failedEntries();

  /// 清空本地备份记录（不影响远端文件）
  Future<void> clearRecords() async {
    await _store.clearAll();
    await emitProgress();
  }

  /// 释放资源
  Future<void> dispose() async {
    _stopTimer();
    _stopConnectivity();
    await _taskSub?.cancel();
    _taskSub = null;
    await _progress.close();
    await _activity.close();
    await _store.close();
  }

  // ── 内部实现 ──────────────────────────────────────

  void _startTimer() {
    _timer?.cancel();
    _timer = Timer.periodic(
      const Duration(seconds: 60),
      (_) => scanAndUpload(),
    );
  }

  void _stopTimer() {
    _timer?.cancel();
    _timer = null;
  }

  /// Wi-Fi/以太网恢复时**立即**补一次扫描（issue #6 ①：不必等满 60 秒）。
  void _watchConnectivity() {
    _connectivitySub ??= Connectivity().onConnectivityChanged.listen((results) {
      final onWifi =
          results.contains(ConnectivityResult.wifi) ||
          results.contains(ConnectivityResult.ethernet);
      if (onWifi) unawaited(scanAndUpload());
    });
  }

  void _stopConnectivity() {
    _connectivitySub?.cancel();
    _connectivitySub = null;
  }

  /// 判断当前是否满足「仅 Wi-Fi / 仅充电」条件
  Future<bool> _conditionsMet() async {
    if (_config.wifiOnly) {
      final results = await Connectivity().checkConnectivity();
      final onWifi =
          results.contains(ConnectivityResult.wifi) ||
          results.contains(ConnectivityResult.ethernet);
      if (!onWifi) return false;
    }
    if (_config.chargingOnly) {
      final state = await Battery().batteryState;
      if (state != BatteryState.charging && state != BatteryState.full) {
        return false;
      }
    }
    return true;
  }

  /// 媒体类型是否在备份范围内
  bool _matchesType(AlbumAsset asset) {
    if (asset.kind == AlbumMediaKind.image) return _config.includePhotos;
    if (asset.kind == AlbumMediaKind.video) return _config.includeVideos;
    return false;
  }

  /// 远端目标目录：`<用户选定根>/YYYY/MM`（按日期分层，避免单目录上万文件）
  String _remoteDirFor(AlbumAsset asset, String remoteRoot) {
    final t = asset.createDateTime;
    final year = t.year.toString().padLeft(4, '0');
    final month = t.month.toString().padLeft(2, '0');
    return remoteRoot.isEmpty ? '$year/$month' : '$remoteRoot/$year/$month';
  }

  /// 处理单个资源：取文件 → 远端比对 → 提交上传
  ///
  /// **注意**：本方法内的每一步都可能抛异常（平台通道 / IO / 网络），
  /// 由调用方按单个资源 `try/catch`，不得冒泡中止整轮。
  Future<_HandleOutcome> _handleAsset(
    AlbumAsset asset,
    int diskId,
    String remoteRoot, {
    required Set<String> backed,
  }) async {
    if (!_matchesType(asset)) return _HandleOutcome.skipped;

    final mediaKind = asset.kind == AlbumMediaKind.video ? 'video' : 'image';
    final remoteDir = _remoteDirFor(asset, remoteRoot);
    var filename = asset.filename;

    // 远端优先：查**本地持久化索引**，命中同名即视为已备份 —— 不打开本地文件。
    // 索引未建立完成 ⇒ 暂停上传（deferred），绝不盲传。
    if (_remoteIndex != null) {
      if (!_remoteIndexReady) return _HandleOutcome.deferred;
      final dirIndex = _remoteIndexData[remoteDir];
      final remoteSize = dirIndex?[filename];
      if (remoteSize != null) {
        _lastSkippedExisting++;
        if (!backed.contains(asset.id)) {
          await _store.markBackedUp(
            AlbumBackupRecord(
              assetId: asset.id,
              diskId: diskId,
              remoteRoot: remoteRoot,
              remotePath: remoteDir.isEmpty ? filename : '$remoteDir/$filename',
              size: remoteSize,
              backedAt: DateTime.now().toIso8601String(),
              mediaKind: mediaKind,
            ),
          );
        }
        return _HandleOutcome.skippedExisting;
      }
      // 索引中没有该文件（含「远端被删除」）：即使本地有记录也重新上传。
    }

    final File? file;
    try {
      file = await asset.file;
    } catch (_) {
      await _markFailed(asset.id, AlbumBackupFailureReason.unavailable);
      return _HandleOutcome.skipped;
    }
    if (file == null) {
      await _markFailed(asset.id, AlbumBackupFailureReason.unavailable);
      return _HandleOutcome.skipped;
    }

    final int size;
    try {
      size = await file.length();
    } catch (_) {
      await _markFailed(asset.id, AlbumBackupFailureReason.readFailed);
      return _HandleOutcome.skipped;
    }
    if (size <= 0) {
      await _markFailed(asset.id, AlbumBackupFailureReason.readFailed);
      return _HandleOutcome.skipped;
    }

    // v1.8.3：title 为空/无扩展名时补上本地文件扩展名，避免远端出现无后缀文件
    if (!filename.contains('.')) {
      final ext = p.extension(file.path);
      if (ext.isNotEmpty) filename = '$filename$ext';
    }
    final remotePath = remoteDir.isEmpty ? filename : '$remoteDir/$filename';

    try {
      final taskId = await _transfer.submitUpload(
        diskId,
        remoteDir,
        file.path,
        filename,
        size,
        tag: 'album:${asset.id}',
      );
      _pending[taskId] = _PendingUpload(
        assetId: asset.id,
        diskId: diskId,
        remoteRoot: remoteRoot,
        remotePath: remotePath,
        remoteDir: remoteDir,
        size: size,
        mediaKind: mediaKind,
      );
      return _HandleOutcome.submitted;
    } catch (_) {
      await _markFailed(asset.id, AlbumBackupFailureReason.uploadFailed);
      return _HandleOutcome.skipped;
    }
  }

  /// 建立/刷新本地持久化远端索引（只在索引缺失、过期、手动检查时执行）
  ///
  /// 约束（用户裁决）：
  ///  - 建索引期间**不开始上传**；索引未就绪时上传一律 deferred；
  ///  - 正在上传（有 pending 任务）时**不重建索引**，避免覆盖本地已维护的索引；
  ///  - 只列目录与文件名/大小，不读取远端文件内容。
  Future<bool> _ensureRemoteIndex({required bool force}) async {
    if (_remoteIndex == null) return true;
    if (_indexBuilding) return false;
    final fresh =
        _remoteIndexReady &&
        _remoteIndexBuiltAt != null &&
        DateTime.now().difference(_remoteIndexBuiltAt!) < remoteIndexTtl;
    if (!force && fresh) return true;
    // 正在上传时禁止重建（用现有索引继续；没有索引则由调用方暂停上传）
    if (_pending.isNotEmpty) return _remoteIndexReady;

    _indexBuilding = true;
    _emitActivity(
      phase: AlbumBackupPhase.checkingRemote,
      message: '正在建立远端索引…',
      processed: 0,
      total: 0,
      cancellable: true,
      force: true,
    );
    try {
      final entries = <String, Map<String, int>>{};
      final queue = <String>[_remoteRoot];
      final visited = <String>{};
      while (queue.isNotEmpty && !_cancelRequested) {
        final dir = queue.removeAt(0);
        if (!visited.add(dir)) continue;
        final List<RemoteFileEntry> list;
        try {
          list = await _remoteIndex.listFiles(_config.diskId, dir);
        } catch (e) {
          if (_isMissingDirectory(e)) continue;
          _remoteCheckFailed++;
          _remoteIndexReady = false;
          return false;
        }
        final byName = <String, int>{};
        for (final entry in list) {
          if (entry.isDir) {
            queue.add(dir.isEmpty ? entry.name : '$dir/${entry.name}');
          } else {
            byName[entry.name] = entry.size;
          }
        }
        if (byName.isNotEmpty) entries[dir] = byName;
        _emitActivity(
          message: '正在建立远端索引（已扫描 ${visited.length} 个目录）',
          processed: visited.length,
          total: 0,
          cancellable: true,
        );
      }
      if (_cancelRequested) return false;
      await _store.replaceRemoteIndex(
        diskId: _config.diskId,
        remoteRoot: _remoteRoot,
        entries: entries,
      );
      _remoteIndexData = entries;
      _remoteIndexReady = true;
      _remoteIndexBuiltAt = DateTime.now();
      return true;
    } finally {
      _indexBuilding = false;
    }
  }

  /// 判断异常是否为「目录不存在」（服务端 `list_files` 对不存在的路径抛 404/400）
  bool _isMissingDirectory(Object error) {
    if (error is! AppException) return false;
    if (error.code == 404) return true;
    final message = error.message;
    return message.contains('路径不存在') || message.contains('不是目录');
  }

  /// 写失败记录（自身异常也不得冒泡）
  Future<void> _markFailed(String assetId, String reason) async {
    try {
      await _store.markFailed(assetId, reason);
    } catch (e) {
      debugPrint('[相册备份] 记录失败状态失败: ${e.runtimeType}');
    }
  }

  /// 传输任务状态回调：把成功/失败回写到本地记录
  void _onTasks(List<TransferTask> tasks) {
    var changed = false;
    for (final task in tasks) {
      final pending = _pending[task.id];
      if (pending == null) continue;

      if (task.status == TaskStatus.completed) {
        _pending.remove(task.id);
        _recentlyDone.add(pending.assetId);
        changed = true;
        unawaited(
          _store.markBackedUp(
            AlbumBackupRecord(
              assetId: pending.assetId,
              diskId: pending.diskId,
              remoteRoot: pending.remoteRoot,
              remotePath: pending.remotePath,
              size: pending.size,
              backedAt: DateTime.now().toIso8601String(),
              mediaKind: pending.mediaKind,
            ),
          ),
        );
        // 上传成功后立即维护本地索引，避免下轮重复上传
        final remoteDir = pending.remoteDir;
        final remoteName = pending.remotePath.split('/').last;
        _remoteIndexData.putIfAbsent(remoteDir, () => {})[remoteName] =
            pending.size;
        unawaited(
          _store.upsertRemoteFile(
            diskId: pending.diskId,
            remoteRoot: pending.remoteRoot,
            dir: remoteDir,
            name: remoteName,
            size: pending.size,
          ),
        );
      } else if (task.status == TaskStatus.failed ||
          task.status == TaskStatus.cancelled) {
        _pending.remove(task.id);
        changed = true;
        final reason = task.status == TaskStatus.cancelled
            ? AlbumBackupFailureReason.cancelled
            : AlbumBackupFailureReason.uploadFailed;
        unawaited(_markFailed(pending.assetId, reason));
      }
    }
    if (changed) unawaited(emitProgress());
  }
}
