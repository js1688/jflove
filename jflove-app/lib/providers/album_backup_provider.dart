import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../services/album_backup/album_backup_config.dart';
import '../services/album_backup/album_backup_service.dart';
import '../services/album_backup/remote_file_index.dart';
import 'file_provider.dart';
import 'transfer_provider.dart';

/// 相册备份服务（全局单例）
///
/// 依赖 `transferServiceProvider`：上传仍复用既有加密分片通道；
/// 依赖 `fileServiceProvider`：远端目录比对走同一套加密 GET。
final albumBackupServiceProvider = Provider<AlbumBackupService>((ref) {
  final service = AlbumBackupService(
    ref.watch(transferServiceProvider),
    remoteIndex: FileServiceRemoteFileIndex(ref.watch(fileServiceProvider)),
  );
  ref.onDispose(() {
    unawaited(service.dispose());
  });
  return service;
});

/// 相册备份配置（首次读取时顺带触发服务初始化）
final albumBackupConfigProvider = FutureProvider<AlbumBackupConfig>((
  ref,
) async {
  final service = ref.watch(albumBackupServiceProvider);
  await service.init();
  return service.config;
});

/// 相册备份进度流（先发当前快照，再转发后续更新）
final albumBackupProgressProvider = StreamProvider<AlbumBackupProgress>((
  ref,
) async* {
  final service = ref.watch(albumBackupServiceProvider);
  await service.init();
  yield await service.emitProgress();
  yield* service.progressStream;
});
