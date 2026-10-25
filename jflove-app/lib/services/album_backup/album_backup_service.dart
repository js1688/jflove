import 'dart:async';
import 'dart:io';

import 'package:battery_plus/battery_plus.dart';
import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';

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

  /// 本轮是否存在远端目录列表失败（fail-open）
  final bool remoteCheckUnavailable;

  const AlbumBackupProgress({
    this.total = 0,
    this.pending = 0,
    this.uploading = 0,
    this.done = 0,
    this.failed = 0,
    this.outOfScope = 0,
    this.skippedExisting = 0,
    this.remoteCheckUnavailable = false,
  });

  static const AlbumBackupProgress empty = AlbumBackupProgress();
}

/// 待确认结果的上传任务
class _PendingUpload {
  final String assetId;
  final int diskId;
  final String remoteRoot;
  final String remotePath;
  final int size;

  const _PendingUpload({
    required this.assetId,
    required this.diskId,
    required this.remoteRoot,
    required this.remotePath,
    required this.size,
  });
}

/// 单个资源的处理结果
enum _HandleOutcome {
  /// 已提交上传
  submitted,

  /// 远端已存在同名同大小文件（已补本地记录）
  skippedExisting,

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
  StreamSubscription<List<TransferTask>>? _taskSub;
  StreamSubscription<List<ConnectivityResult>>? _connectivitySub;

  /// 刚完成但可能还没写入 sqflite 的资源（issue #6：防扫描窗口内重复提交）
  final Set<String> _recentlyDone = {};

  /// 跨轮游标：下一轮从该偏移继续（扫到尾部回绕 0）
  int _cursorOffset = 0;

  /// 本轮远端目录索引：`diskId:relDir` → {文件名: 大小}；null = 该目录不可用
  final Map<String, Map<String, int>?> _remoteIndexCache = {};

  /// 本轮远端目录请求次数（上限 30，防止一次扫描打爆接口）
  int _remoteIndexRequests = 0;

  /// 本轮远端列表失败的目录数（>0 时界面提示「远端校验暂不可用」）
  int _remoteCheckFailed = 0;

  /// 本轮「远端已存在跳过」计数
  int _lastSkippedExisting = 0;

  /// 单轮最多提交的上传任务数（避免一次塞爆传输队列）
  static const int _maxSubmitPerRound = 20;

  /// 单轮最多访问的媒体数量（跨轮推进，不再永久饿死 >2000 的相册）
  static const int _maxScanPerRound = 2000;

  /// 分页大小（photo_manager 单次查询条数）
  static const int _pageSize = 200;

  /// 单个失败项自动重试上限
  static const int _maxAutoRetries = 3;

  /// 单轮最多请求的远端目录数
  static const int _maxRemoteDirRequests = 30;

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
    if (_autoStart &&
        _config.enabled &&
        _config.isConfigured &&
        _config.hasMediaType) {
      _startTimer();
      _watchConnectivity();
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
      // 换磁盘/目标目录后，旧目标下的失败状态不再适用：清空失败记录并
      // 让游标从头开始，保证历史资源进入新目标的待传队列（F38.2 / AC-282）。
      await _store.clearAllFailures();
      _cursorOffset = 0;
      _remoteIndexCache.clear();
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
      remoteCheckUnavailable: _remoteCheckFailed > 0,
    );
    if (!_progress.isClosed) _progress.add(snapshot);
    return snapshot;
  }

  /// 立即扫描并提交上传（幂等：已在处理中则直接返回）
  ///
  /// [manual] 为 true 时（「立即检查」）清空失败记录，等价于手动解锁自动重试。
  Future<void> scanAndUpload({bool manual = false}) async {
    if (_scanning || !_config.enabled || !_config.isConfigured) return;
    if (!_config.hasMediaType) return;
    if (!await _conditionsMet()) return;

    _scanning = true;
    _remoteIndexCache.clear();
    _remoteIndexRequests = 0;
    _remoteCheckFailed = 0;
    _lastSkippedExisting = 0;
    try {
      if (!await _assetSource.ensurePermission()) return;
      if (manual) await _store.clearAllFailures();

      final diskId = _config.diskId;
      final remoteRoot = _remoteRoot;
      final backed = await _store.backedUpIds(
        diskId: diskId,
        remoteRoot: remoteRoot,
      );
      final failures = await _store.failedEntries();
      final failureById = {for (final f in failures) f.assetId: f};
      final pendingIds = _pending.values.map((item) => item.assetId).toSet();
      final attempted = <String>{};
      var submitted = 0;

      // ① 失败项自动重试：每轮最多 1 次；累计 3 次后暂停（手动可解锁）
      for (final failure in failures) {
        if (submitted >= _maxSubmitPerRound) break;
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
          final outcome = await _handleAsset(asset, diskId, remoteRoot);
          if (outcome == _HandleOutcome.submitted) {
            submitted++;
            pendingIds.add(asset.id);
          } else if (outcome == _HandleOutcome.skippedExisting) {
            backed.add(asset.id);
          }
        } catch (_) {
          await _markFailed(asset.id, AlbumBackupFailureReason.readFailed);
        }
      }

      // ② 跨轮游标扫描：从上次位置继续，扫到尾部回绕到 0
      var pageStart = _cursorOffset;
      var visited = 0;
      while (visited < _maxScanPerRound && submitted < _maxSubmitPerRound) {
        final assets = await _assetSource.listAssets(
          start: pageStart,
          end: pageStart + _pageSize,
        );
        if (assets.isEmpty) {
          if (pageStart == 0) {
            _cursorOffset = 0;
            break;
          }
          // 相册尾部：回绕到头部继续（坏资源/新资源不会永久饿死）
          pageStart = 0;
          _cursorOffset = 0;
          continue;
        }

        for (var i = 0; i < assets.length; i++) {
          if (visited >= _maxScanPerRound || submitted >= _maxSubmitPerRound) {
            break;
          }
          final asset = assets[i];
          visited++;
          _cursorOffset = pageStart + i + 1;

          if (backed.contains(asset.id) ||
              pendingIds.contains(asset.id) ||
              _recentlyDone.contains(asset.id)) {
            continue;
          }
          final priorFailure = failureById[asset.id];
          if (priorFailure != null &&
              (priorFailure.attempts >= _maxAutoRetries ||
                  attempted.contains(asset.id))) {
            continue;
          }
          if (!_matchesType(asset)) continue;

          // 单个资源独立 try/catch：任何异常都不得中止整轮
          try {
            final outcome = await _handleAsset(asset, diskId, remoteRoot);
            if (outcome == _HandleOutcome.submitted) {
              submitted++;
              pendingIds.add(asset.id);
            } else if (outcome == _HandleOutcome.skippedExisting) {
              backed.add(asset.id);
            }
          } catch (_) {
            await _markFailed(asset.id, AlbumBackupFailureReason.readFailed);
          }
        }

        if (submitted >= _maxSubmitPerRound || visited >= _maxScanPerRound) {
          break;
        }
        if (assets.length < _pageSize) {
          // 已到相册尾部：下一轮从头开始
          _cursorOffset = 0;
          break;
        }
        pageStart += assets.length;
        _cursorOffset = pageStart;
      }
    } catch (e) {
      // 扫描级失败（权限/平台异常）不静默：写日志与失败计数，不输出文件名
      debugPrint('[相册备份] 扫描失败: ${e.runtimeType}');
    } finally {
      _scanning = false;
      _remoteIndexCache.clear();
      await emitProgress();
    }
  }

  /// 「立即检查」：手动解锁失败项后立刻扫描一次
  Future<void> checkNow() => scanAndUpload(manual: true);

  /// 「重新核对远端」：清空本轮缓存并重新扫描（不清失败记录）
  Future<void> recheckRemote() async {
    _remoteIndexCache.clear();
    await scanAndUpload();
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
    String remoteRoot,
  ) async {
    if (!_matchesType(asset)) return _HandleOutcome.skipped;

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

    final filename = asset.filename;
    final remoteDir = _remoteDirFor(asset, remoteRoot);
    final remotePath = remoteDir.isEmpty ? filename : '$remoteDir/$filename';

    // 远端去重：同名同大小 ⇒ 视为已备份（只读，失败 fail-open）
    if (_remoteIndex != null) {
      final index = await _remoteIndexFor(diskId, remoteDir);
      if (index != null && index[filename] == size) {
        _lastSkippedExisting++;
        await _store.markBackedUp(
          AlbumBackupRecord(
            assetId: asset.id,
            diskId: diskId,
            remoteRoot: remoteRoot,
            remotePath: remotePath,
            size: size,
            backedAt: DateTime.now().toIso8601String(),
          ),
        );
        return _HandleOutcome.skippedExisting;
      }
    }

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
        size: size,
      );
      return _HandleOutcome.submitted;
    } catch (_) {
      await _markFailed(asset.id, AlbumBackupFailureReason.uploadFailed);
      return _HandleOutcome.skipped;
    }
  }

  /// 读取（并缓存）远端目录索引；失败返回 null，由调用方 fail-open
  ///
  /// 同一轮扫描内每个目录只请求一次；单轮最多 `_maxRemoteDirRequests` 次。
  Future<Map<String, int>?> _remoteIndexFor(
    int diskId,
    String remoteDir,
  ) async {
    final key = '$diskId:$remoteDir';
    if (_remoteIndexCache.containsKey(key)) return _remoteIndexCache[key];
    if (_remoteIndexRequests >= _maxRemoteDirRequests) {
      // 超出本轮请求预算：按本地记录继续（fail-open）
      _remoteIndexCache[key] = null;
      _remoteCheckFailed++;
      return null;
    }
    _remoteIndexRequests++;
    try {
      final entries = await _remoteIndex!.listFiles(diskId, remoteDir);
      final index = <String, int>{
        for (final entry in entries)
          if (!entry.isDir) entry.name: entry.size,
      };
      _remoteIndexCache[key] = index;
      return index;
    } catch (e) {
      // 首次备份时目标 `YYYY/MM` 目录尚不存在：等价于「空目录」，不算校验失败
      if (_isMissingDirectory(e)) {
        const empty = <String, int>{};
        _remoteIndexCache[key] = empty;
        return empty;
      }
      // 网络/权限失败：本轮该目录退回「按本地记录判断」
      _remoteIndexCache[key] = null;
      _remoteCheckFailed++;
      return null;
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
            ),
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
