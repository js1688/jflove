import 'dart:async';
import 'dart:io';

import '../models/sync_config.dart';
import '../utils/http_service.dart';
import 'sync_service.dart';

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
class SyncEngineService {
  // ignore: unused_field - reserved for future HTTP-based sync
  final HttpService _http;
  final SyncService _syncService;

  final StreamController<SyncEvent> _eventController =
      StreamController<SyncEvent>.broadcast();

  Timer? _autoSyncTimer;
  List<SyncConfig> _configs = [];
  final Set<String> _runningConfigs = {};

  SyncEngineService(this._http, this._syncService);

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
      final remoteFiles = await _syncService.fetchSnapshot(
        config.diskId,
        config.remotePath,
      );
      final localFiles = await _scanLocalFiles(config.localPath);

      int uploaded = 0, downloaded = 0, skipped = 0;

      for (final local in localFiles) {
        final remote = remoteFiles.where((r) => r['path'] == local);
        if (remote.isEmpty) {
          uploaded++;
        } else {
          skipped++;
        }
      }

      for (final remote in remoteFiles) {
        final path = remote['path'] as String? ?? '';
        if (path.isNotEmpty && !localFiles.contains(path)) {
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

  Future<List<String>> _scanLocalFiles(String localPath) async {
    final dir = Directory(localPath);
    if (!await dir.exists()) return [];
    final files = <String>[];
    await for (final entity in dir.list()) {
      if (entity is File) {
        files.add(entity.uri.pathSegments.last);
      }
    }
    return files;
  }
}
