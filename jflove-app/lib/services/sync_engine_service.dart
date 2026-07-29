import 'dart:async';
import 'dart:io';

import '../models/sync_config.dart';
import '../utils/http_service.dart';
import 'sync_service.dart';
import 'transfer_service.dart';

/// 本地文件信息（同步扫描用）
class _LocalFileInfo {
  final String relPath; // 相对于 sync localPath 的相对路径
  final int size;
  const _LocalFileInfo({required this.relPath, required this.size});
}

/// 同步引擎事件
sealed class SyncEvent {}

class SyncStarted extends SyncEvent {
  final String configId;
  SyncStarted(this.configId);
}

class SyncProgress extends SyncEvent {
  final String configId;
  final int uploaded;
  final int downloaded;
  final int skipped;
  SyncProgress(this.configId, this.uploaded, this.downloaded, this.skipped);
}

class SyncFinished extends SyncEvent {
  final String configId;
  final int uploaded;
  final int downloaded;
  final int skipped;
  SyncFinished(this.configId, this.uploaded, this.downloaded, this.skipped);
}

class SyncError extends SyncEvent {
  final String configId;
  final String error;
  SyncError(this.configId, this.error);
}

/// 同步引擎
///
/// 负责对比本地与远端文件差异，并通过 TransferService 执行实际的上传/下载。
/// 传输进度自动反映在传输任务页面中。
class SyncEngineService {
  // ignore: unused_field - reserved for future HTTP-based sync
  final HttpService _http;
  final SyncService _syncService;
  final TransferService _transferService;

  final StreamController<SyncEvent> _eventController =
      StreamController<SyncEvent>.broadcast();

  Timer? _autoSyncTimer;
  List<SyncConfig> _configs = [];
  final Set<String> _runningConfigs = {};

  SyncEngineService(this._http, this._syncService, this._transferService);

  Stream<SyncEvent> get eventStream => _eventController.stream;

  void reloadConfigs(List<SyncConfig> configs) {
    _configs = configs;
    _restartAutoSync();
  }

  Future<bool> triggerSync(String configId) async {
    if (_runningConfigs.contains(configId)) return false;

    final config = _configs.firstWhere(
      (c) => c.id == configId,
      orElse: () => const SyncConfig(id: '', diskId: 0, localPath: ''),
    );
    if (config.id.isEmpty) return false;

    _runningConfigs.add(configId);
    _eventController.add(SyncStarted(configId));

    try {
      // 确保本地目录存在
      final localDir = Directory(config.localPath);
      if (!await localDir.exists()) {
        await localDir.create(recursive: true);
      }

      final remoteFiles = await _syncService.fetchSnapshot(
        config.diskId,
        config.remotePath,
      );
      final localFiles = await _scanLocalFiles(config.localPath);

      // 构建远端文件集合（文件名 → 大小）
      final remoteMap = <String, int>{};
      for (final rf in remoteFiles) {
        final name = (rf['path'] as String?) ?? '';
        final size = (rf['size'] as int?) ?? 0;
        if (name.isNotEmpty) remoteMap[name] = size;
      }

      // 构建本地文件集合（文件名 → _LocalFileInfo）
      final localMap = <String, _LocalFileInfo>{};
      for (final lf in localFiles) {
        localMap[lf.relPath] = lf;
      }

      int uploaded = 0, downloaded = 0, skipped = 0;

      // 本地有、远端没有 → 上传
      for (final entry in localMap.entries) {
        if (!remoteMap.containsKey(entry.key)) {
          final localFullPath = '${config.localPath}/${entry.key}';
          await _transferService.submitUpload(
            config.diskId,
            config.remotePath,
            localFullPath,
            entry.key,
            entry.value.size,
          );
          uploaded++;
        } else {
          skipped++;
        }
      }

      // 远端有、本地没有 → 下载
      for (final entry in remoteMap.entries) {
        if (!localMap.containsKey(entry.key)) {
          final localFullPath = '${config.localPath}/${entry.key}';
          final remoteFilePath = config.remotePath.isEmpty
              ? entry.key
              : '${config.remotePath}/${entry.key}';
          await _transferService.submitDownload(
            config.diskId,
            remoteFilePath,
            localFullPath,
            entry.key,
            entry.value,
          );
          downloaded++;
        }
      }

      _eventController.add(
        SyncProgress(configId, uploaded, downloaded, skipped),
      );
      _eventController.add(
        SyncFinished(configId, uploaded, downloaded, skipped),
      );
      return true;
    } catch (e) {
      _eventController.add(SyncError(configId, e.toString()));
      return false;
    } finally {
      _runningConfigs.remove(configId);
    }
  }

  void startAutoSync() {
    _restartAutoSync();
  }

  void stopAutoSync() {
    _autoSyncTimer?.cancel();
    _autoSyncTimer = null;
  }

  void dispose() {
    stopAutoSync();
    _eventController.close();
  }

  void _restartAutoSync() {
    _autoSyncTimer?.cancel();
    _autoSyncTimer = null;

    final enabledConfigs = _configs
        .where((c) => c.autoSync && c.enabled)
        .toList();
    if (enabledConfigs.isEmpty) return;

    final minInterval = enabledConfigs
        .map((c) => c.syncInterval)
        .reduce((a, b) => a < b ? a : b);
    _autoSyncTimer = Timer.periodic(Duration(seconds: minInterval), (_) {
      for (final config in enabledConfigs) {
        triggerSync(config.id);
      }
    });
  }

  Future<List<_LocalFileInfo>> _scanLocalFiles(String localPath) async {
    final dir = Directory(localPath);
    if (!await dir.exists()) return [];
    final files = <_LocalFileInfo>[];
    await for (final entity in dir.list()) {
      if (entity is File) {
        final name = entity.uri.pathSegments.last;
        final size = await entity.length();
        files.add(_LocalFileInfo(relPath: name, size: size));
      }
    }
    return files;
  }
}
