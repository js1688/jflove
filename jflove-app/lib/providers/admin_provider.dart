import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/disk_permission.dart';
import '../models/user.dart';
import '../models/virtual_disk.dart';
import '../services/permission_service.dart';
import '../services/user_service.dart';
import 'file_provider.dart';
import 'session_provider.dart';

final userServiceProvider = Provider<UserService>((ref) {
  return UserService(ref.watch(httpServiceProvider));
});

final permissionServiceProvider = Provider<PermissionService>((ref) {
  return PermissionService(ref.watch(httpServiceProvider));
});

final userListProvider = FutureProvider<List<User>>((ref) async {
  return ref.watch(userServiceProvider).listUsers();
});

final adminDiskListProvider = FutureProvider<List<VirtualDisk>>((ref) async {
  return ref.watch(diskServiceProvider).listDisks();
});

final userPermissionsProvider =
    FutureProvider.family<List<DiskPermission>, int>((ref, userId) async {
      final rawPerms = await ref
          .watch(permissionServiceProvider)
          .listPermissions(userId);
      return rawPerms.map((e) => DiskPermission.fromJson(e)).toList();
    });
