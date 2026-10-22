/// 离线下载路由（v1.8.0 / v1.8.1）
///
/// 说明：历史路由仍以内联字面量写在 `lib/app.dart` 的 `GoRoute` 里
///（另有 `_AppScaffold._routes` 维护底部导航），本版**不**做整体搬迁，
/// 避免一次改动波及旧书签 / 通知跳转与既有回归测试。
/// 新页面的路径统一在本文件集中定义，供 `app.dart` 与页面入口引用。
class AppRoutes {
  const AppRoutes._();

  /// 离线下载中心（v1.8.1 起为完整功能：新建 / 操作 / 设置）
  ///
  /// 页面：`lib/pages/offline/offline_download_page.dart`
  /// 入口：设置页 →「离线下载」→「离线下载中心」（需求 F30）。
  static const String offlineDownload = '/offline';

  /// 新建离线下载任务（v1.8.1 / F32.2）
  ///
  /// 页面：`lib/pages/offline/offline_new_task_page.dart`
  static const String offlineNew = '/offline/new';
}
