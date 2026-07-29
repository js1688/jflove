import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/sync_config.dart';
import '../services/sync_engine_service.dart';
import '../services/sync_service.dart';
import 'session_provider.dart';
import 'transfer_provider.dart';

final syncServiceProvider = Provider<SyncService>((ref) {
  return SyncService(
    ref.watch(httpServiceProvider),
    ref.watch(sessionManagerProvider),
  );
});

final syncEngineServiceProvider = Provider<SyncEngineService>((ref) {
  return SyncEngineService(
    ref.watch(httpServiceProvider),
    ref.watch(syncServiceProvider),
    ref.watch(transferServiceProvider),
  );
});

final syncConfigListProvider = FutureProvider<List<SyncConfig>>((ref) async {
  final syncService = ref.watch(syncServiceProvider);
  return syncService.loadConfigs();
});
