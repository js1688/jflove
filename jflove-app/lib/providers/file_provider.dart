import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/file_item.dart';
import '../models/virtual_disk.dart';
import '../services/disk_service.dart';
import '../services/file_service.dart';
import '../services/repair_service.dart';
import 'session_provider.dart';

/// 文件服务
final fileServiceProvider = Provider<FileService>((ref) {
  return FileService(ref.watch(httpServiceProvider));
});

/// 媒体修复服务（v1.4.2）
final repairServiceProvider = Provider<RepairService>((ref) {
  return RepairService(ref.watch(httpServiceProvider));
});

/// 磁盘服务
final diskServiceProvider = Provider<DiskService>((ref) {
  return DiskService(ref.watch(httpServiceProvider));
});

// 注意：transferServiceProvider / transferTaskStreamProvider / transferStatsProvider
// 统一定义在 transfer_provider.dart 中，此处不再重复定义，
// 避免文件管理页与传输任务页使用不同的 TransferService 实例导致任务不互通。

/// 可访问磁盘列表（用户视角，非管理员也适用）
/// 调用 GET /api/v1/files/disks，非 admin-only 的 GET /api/v1/virtual-disks
final accessibleDiskListProvider = FutureProvider<List<VirtualDisk>>((
  ref,
) async {
  return ref.watch(diskServiceProvider).listAccessibleDisks();
});

/// 文件列表（按 diskId + path）
final fileListProvider =
    FutureProvider.family<List<FileItem>, ({int diskId, String path})>((
      ref,
      params,
    ) async {
      return ref
          .watch(fileServiceProvider)
          .listFiles(params.diskId, path: params.path);
    });
