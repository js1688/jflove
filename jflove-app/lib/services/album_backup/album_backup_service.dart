import 'dart:async';

import 'package:battery_plus/battery_plus.dart';
import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';
import 'package:photo_manager/photo_manager.dart';

import '../../models/transfer_task.dart';
import '../transfer_service.dart';
import 'album_backup_config.dart';
import 'album_backup_store.dart';

/// 相册备份进度快照（传输页展示用）
class AlbumBackupProgress {
  /// 待备份数量（当前实现按本轮扫描上限估算，0 表示暂无）
  final int pending;

  /// 正在上传数量
  final int uploading;

  /// 已备份总数
  final int done;

  /// 失败数量
  final int failed;

  const AlbumBackupProgress({
    this.pending = 0,
    this.uploading = 0,
    this.done = 0,
    this.failed = 0,
  });

  static const AlbumBackupProgress empty = AlbumBackupProgress();
}

/// 待确认结果的上传任务
class _PendingUpload {
  final String assetId;
  final String remotePath;
  final int size;

  const _PendingUpload({
    required this.assetId,
    required this.remotePath,
    required this.size,
  });
}

/// 相册自动备份服务（v1.7.0）
///
/// 职责：
///   1. 开关打开后按 60 秒周期扫描新增媒体并提交加密分片上传；
///   2. 用 sqflite 记录已备份资源，保证「不重复上传」；
///   3. 订阅 `TransferService.taskStream`，把上传结果回写成记录（成功/失败）；
///   4. 对外广播进度，供设置页与传输页展示。
///
/// 安全说明：
///   - 上传全部走 `TransferService`（内部只经 `http_service.dart` 加密通道）；
///   - 本地记录只存 asset_id / 远端路径 / 大小，不存图片内容与文件名内容。
class AlbumBackupService {
  final TransferService _transfer;
  final AlbumBackupStore _store;
  final AlbumBackupConfigStore _configStore;

  AlbumBackupConfig _config = AlbumBackupConfig.empty;
  Timer? _timer;
  bool _scanning = false;
  final Map<String, _PendingUpload> _pending = {};
  final StreamController<AlbumBackupProgress> _progress =
      StreamController<AlbumBackupProgress>.broadcast();
  StreamSubscription<List<TransferTask>>? _taskSub;

  /// 单轮最多提交的上传任务数（避免一次塞爆传输队列）
  static const int _maxSubmitPerRound = 20;

  /// 单轮最多扫描的媒体数量
  static const int _maxScanPerRound = 2000;

  /// 分页大小（photo_manager 单次查询条数）
  static const int _pageSize = 200;

  AlbumBackupService(
    this._transfer, {
    AlbumBackupStore? store,
    AlbumBackupConfigStore? configStore,
  })  : _store = store ?? AlbumBackupStore(),
        _configStore = configStore ?? AlbumBackupConfigStore() {
    _taskSub = _transfer.taskStream.listen(_onTasks);
  }

  /// 当前配置
  AlbumBackupConfig get config => _config;

  /// 进度流
  Stream<AlbumBackupProgress> get progressStream => _progress.stream;

  /// 初始化：读本地配置，开关已打开则启动周期扫描
  Future<void> init() async {
    _config = await _configStore.load();
    if (_config.enabled && _config.isConfigured && _config.hasMediaType) {
      _startTimer();
    }
    await emitProgress();
  }

  /// 保存配置；开关打开时立即启动扫描，关闭时停止调度
  Future<void> setConfig(AlbumBackupConfig config) async {
    _config = config;
    await _configStore.save(config);
    if (config.enabled && config.isConfigured && config.hasMediaType) {
      _startTimer();
      unawaited(scanAndUpload());
    } else {
      _stopTimer();
    }
    await emitProgress();
  }

  /// 请求相册读取权限（Android 13+ 分媒体类型）
  Future<bool> ensurePermission() async {
    final state = await PhotoManager.requestPermissionExtend();
    return state.hasAccess;
  }

  /// 读取一次当前进度（供页面首次渲染）
  Future<AlbumBackupProgress> emitProgress() async {
    final done = await _store.backedUpCount();
    final failed = await _store.failedCount();
    final snapshot = AlbumBackupProgress(
      uploading: _pending.length,
      done: done,
      failed: failed,
    );
    if (!_progress.isClosed) _progress.add(snapshot);
    return snapshot;
  }

  /// 立即扫描并提交上传（幂等：已在处理中则直接返回）
  Future<void> scanAndUpload() async {
    if (_scanning || !_config.enabled || !_config.isConfigured) return;
    if (!_config.hasMediaType) return;
    if (!await _conditionsMet()) return;

    _scanning = true;
    try {
      final state = await PhotoManager.requestPermissionExtend();
      if (!state.hasAccess) return;

      final paths = await PhotoManager.getAssetPathList(
        type: RequestType.common,
        onlyAll: true,
      );
      if (paths.isEmpty) return;

      // 分页扫描：不能只扫第一页——否则"最近 200 张已备份"之后，
      // 更早的照片永远轮不到（实测缺陷，审查 v1.7.0 S-1）。
      final backed = await _store.backedUpIds();
      var offset = 0;
      var submitted = 0;
      while (offset < _maxScanPerRound && submitted < _maxSubmitPerRound) {
        final assets = await paths.first.getAssetListRange(
          start: offset,
          end: offset + _pageSize,
        );
        if (assets.isEmpty) break;
        for (final asset in assets) {
          if (submitted >= _maxSubmitPerRound) break;
          if (backed.contains(asset.id)) continue;
          if (!_matchesType(asset)) continue;
          final ok = await _submit(asset);
          if (ok) submitted++;
        }
        if (assets.length < _pageSize) break;
        offset += _pageSize;
      }
    } catch (e) {
      // 扫描失败不静默：写日志，进度流会带出失败数量；具体原因由传输任务展示
      debugPrint('[相册备份] 扫描失败: $e');
    } finally {
      _scanning = false;
      await emitProgress();
    }
  }

  /// 重试全部失败项（先清失败标记，再触发一次扫描）
  Future<void> retryFailed() async {
    final ids = await _store.failedIds();
    for (final id in ids) {
      await _store.clearFailed(id);
    }
    await emitProgress();
    unawaited(scanAndUpload());
  }

  /// 清空本地备份记录（不影响远端文件）
  Future<void> clearRecords() async {
    await _store.clearAll();
    await emitProgress();
  }

  /// 释放资源
  Future<void> dispose() async {
    _stopTimer();
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

  /// 判断当前是否满足「仅 Wi-Fi / 仅充电」条件
  Future<bool> _conditionsMet() async {
    if (_config.wifiOnly) {
      final results = await Connectivity().checkConnectivity();
      final onWifi = results.contains(ConnectivityResult.wifi) ||
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
  bool _matchesType(AssetEntity asset) {
    if (asset.type == AssetType.image) return _config.includePhotos;
    if (asset.type == AssetType.video) return _config.includeVideos;
    return false;
  }

  /// 远端目标目录：`<用户选定根>/YYYY/MM`（按日期分层，避免单目录上万文件）
  String _remoteDirFor(AssetEntity asset) {
    final t = asset.createDateTime;
    final year = t.year.toString().padLeft(4, '0');
    final month = t.month.toString().padLeft(2, '0');
    final base = _config.remotePath.replaceAll(RegExp(r'^/+|/+$'), '');
    return base.isEmpty ? '$year/$month' : '$base/$year/$month';
  }

  /// 提交单个资源的上传任务
  Future<bool> _submit(AssetEntity asset) async {
    final file = await asset.file;
    if (file == null) return false;
    final size = await file.length();
    if (size <= 0) return false;

    final remoteDir = _remoteDirFor(asset);
    final filename = asset.title ?? 'asset_${asset.id}';
    try {
      final taskId = await _transfer.submitUpload(
        _config.diskId,
        remoteDir,
        file.path,
        filename,
        size,
        tag: 'album:${asset.id}',
      );
      _pending[taskId] = _PendingUpload(
        assetId: asset.id,
        remotePath: '$remoteDir/$filename',
        size: size,
      );
      return true;
    } catch (e) {
      await _store.markFailed(asset.id, '提交上传失败: $e');
      return false;
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
        changed = true;
        unawaited(_store.markBackedUp(AlbumBackupRecord(
          assetId: pending.assetId,
          diskId: _config.diskId,
          remotePath: pending.remotePath,
          size: pending.size,
          backedAt: DateTime.now().toIso8601String(),
        )));
      } else if (task.status == TaskStatus.failed ||
          task.status == TaskStatus.cancelled) {
        _pending.remove(task.id);
        changed = true;
        unawaited(_store.markFailed(
          pending.assetId,
          task.error ?? '上传未完成',
        ));
      }
    }
    if (changed) unawaited(emitProgress());
  }
}
