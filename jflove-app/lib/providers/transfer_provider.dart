import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/transfer_task.dart';
import '../services/transfer_service.dart';
import 'session_provider.dart';

/// 数据传输服务
final transferServiceProvider = Provider<TransferService>((ref) {
  return TransferService(ref.watch(httpServiceProvider));
});

/// 传输任务流
final transferTaskStreamProvider = StreamProvider<List<TransferTask>>((ref) {
  final service = ref.watch(transferServiceProvider);
  return service.taskStream;
});

/// 传输统计
final transferStatsProvider = Provider<Map<String, int>>((ref) {
  final service = ref.watch(transferServiceProvider);
  return service.stats();
});
