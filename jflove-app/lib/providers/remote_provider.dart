import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../services/remote_ssh_service.dart';
import 'session_provider.dart';

/// 远程连接 API（走加密信封）
final remoteApiProvider = Provider<RemoteApi>((ref) {
  return RemoteApi(ref.watch(httpServiceProvider));
});

/// 列表页一次性拉分组 + 连接（同一帧渲染，避免两处 loading 闪烁）
class RemoteOverview {
  const RemoteOverview({required this.groups, required this.connections});

  final List<RemoteGroup> groups;
  final List<RemoteConnection> connections;
}

final remoteOverviewProvider = FutureProvider<RemoteOverview>((ref) async {
  final api = ref.watch(remoteApiProvider);
  final results = await Future.wait([api.listGroups(), api.listConnections()]);
  return RemoteOverview(
    groups: results[0] as List<RemoteGroup>,
    connections: results[1] as List<RemoteConnection>,
  );
});
