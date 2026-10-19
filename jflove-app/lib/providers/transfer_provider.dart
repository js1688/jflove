import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/transfer_task.dart';
import '../services/transfer_service.dart';
import 'session_provider.dart';

/// 数据传输服务
final transferServiceProvider = Provider<TransferService>((ref) {
  return TransferService(ref.watch(httpServiceProvider));
});

/// 传输任务流
///
/// 使用 async* 先发射当前任务列表快照（可能为空），再转发后续更新。
/// 解决 StreamProvider 在流无初始事件时一直处于 loading 状态的问题。
final transferTaskStreamProvider = StreamProvider<List<TransferTask>>((ref) {
  final service = ref.watch(transferServiceProvider);
  return _taskStreamWithInitial(service);
});

Stream<List<TransferTask>> _taskStreamWithInitial(
  TransferService service,
) async* {
  // 立即发射当前状态（避免 StreamProvider 一直 loading）
  yield List.unmodifiable(service.tasks);
  // 后续更新通过广播流转发
  yield* service.taskStream;
}

/// 传输统计
///
/// 订阅任务流，否则 `service.stats()` 只会在首次读取时计算，
/// 新增/完成任务后页面上的统计胶囊会一直停在旧值。
final transferStatsProvider = Provider<Map<String, int>>((ref) {
  ref.watch(transferTaskStreamProvider);
  final service = ref.watch(transferServiceProvider);
  return service.stats();
});
