import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../services/offline_download_service.dart';
import 'session_provider.dart';

/// 离线下载服务（v1.8.0，移动端只读）
///
/// 依赖 [httpServiceProvider]（加密信封），与其它 service 的注入方式一致。
final offlineDownloadServiceProvider = Provider<OfflineDownloadService>((ref) {
  return OfflineDownloadService(ref.watch(httpServiceProvider));
});
