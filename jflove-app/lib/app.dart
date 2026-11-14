import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'config/routes.dart';
import 'config/theme.dart';
import 'providers/session_provider.dart';
import 'providers/theme_provider.dart';
import 'pages/login/login_page.dart';
import 'pages/files/file_list_page.dart';
import 'pages/files/disk_browser_page.dart';
import 'pages/files/file_preview_page.dart';
import 'pages/notes/note_list_page.dart';
import 'pages/notes/note_edit_page.dart';
import 'pages/transfer/transfer_page.dart';
import 'pages/offline/offline_download_page.dart';
import 'pages/offline/offline_new_task_page.dart';
import 'pages/settings/settings_page.dart';
import 'pages/album/album_backup_page.dart';
import 'pages/admin/admin_users_page.dart';
import 'pages/admin/admin_disks_page.dart';
import 'pages/admin/admin_permissions_page.dart';
import 'pages/admin/admin_system_page.dart';

/// JFLove 移动端 App
class JFLoveApp extends ConsumerWidget {
  const JFLoveApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final session = ref.watch(sessionManagerProvider);
    final themeMode = ref.watch(themeModeProvider);

    final router = GoRouter(
      initialLocation: '/login',
      redirect: (context, state) {
        final isLoggedIn = session.isLoggedIn;
        final location = state.matchedLocation;

        // 根路径 / 无对应路由，根据登录状态重定向
        if (location == '/') return isLoggedIn ? '/files' : '/login';

        final isLoginRoute = location == '/login';

        if (!isLoggedIn && !isLoginRoute) return '/login';
        if (isLoggedIn && isLoginRoute) return '/files';

        if (location.startsWith('/admin') && !session.isAdmin) {
          return '/files';
        }

        return null;
      },
      routes: [
        GoRoute(path: '/login', builder: (_, _) => const LoginPage()),
        ShellRoute(
          builder: (context, state, child) => _AppScaffold(child: child),
          routes: [
            GoRoute(path: '/files', builder: (_, _) => const FileListPage()),
            GoRoute(path: '/notes', builder: (_, _) => const NoteListPage()),
            // v1.7.0：移除「同步管理」页 —— 移动端改为设置页的「相册自动同步」+
            // 传输页的备份进度，不再提供目录双向同步。
            GoRoute(path: '/transfer', builder: (_, _) => const TransferPage()),
            // v1.8.3：离线下载与相册同步提升为一级入口（底部导航常驻）
            GoRoute(
              path: AppRoutes.offlineDownload,
              builder: (_, _) => const OfflineDownloadPage(),
            ),
            GoRoute(path: '/album', builder: (_, _) => const AlbumBackupPage()),
            // v1.5.0：修复中心并入传输页第二个页签；旧路径 `/repair` 继续可用，
            // 直接落到该页签，避免旧书签 / 通知跳转失效。
            GoRoute(
              path: '/repair',
              builder: (_, _) => const TransferPage(initialTab: 1),
            ),
            GoRoute(path: '/settings', builder: (_, _) => const SettingsPage()),
          ],
        ),
        // 独立页面（不显示底部导航）
        // v1.8.1：离线下载中心（完整功能）—— 入口在设置页，路径集中定义于
        // `lib/config/routes.dart`（`AppRoutes.offlineDownload`）。
        // 注意：`/offline/new` 必须在 `/offline` 之前注册，避免被前缀规则吞掉。
        GoRoute(
          path: AppRoutes.offlineNew,
          builder: (_, _) => const OfflineNewTaskPage(),
        ),
        // 注意：/files/preview 必须在 /files/:diskId 之前，否则 "preview" 会被当作 diskId
        GoRoute(
          path: '/files/preview',
          builder: (_, state) {
            final extra = state.extra as Map<String, dynamic>;
            return FilePreviewPage(
              diskId: extra['disk_id'] as int,
              path: extra['path'] as String,
              name: extra['name'] as String,
            );
          },
        ),
        GoRoute(
          path: '/files/:diskId',
          builder: (_, state) => DiskBrowserPage(
            diskId: int.parse(state.pathParameters['diskId']!),
          ),
        ),
        GoRoute(
          path: '/notes/:noteId',
          builder: (_, state) =>
              NoteEditPage(noteId: state.pathParameters['noteId']!),
        ),
        GoRoute(
          path: '/admin/users',
          builder: (_, _) => const AdminUsersPage(),
        ),
        GoRoute(
          path: '/admin/disks',
          builder: (_, _) => const AdminDisksPage(),
        ),
        GoRoute(
          path: '/admin/permissions',
          builder: (_, _) => const AdminPermissionsPage(),
        ),
        GoRoute(
          path: '/admin/system',
          builder: (_, _) => const AdminSystemPage(),
        ),
      ],
    );

    return MaterialApp.router(
      title: 'JFLove',
      theme: AppTheme.light,
      darkTheme: AppTheme.dark,
      themeMode: toMaterialThemeMode(themeMode),
      routerConfig: router,
      debugShowCheckedModeBanner: false,
    );
  }
}

/// 底部导航框架
///
/// 对标桌面端 FluentWindow + NavigationInterface。
/// v1.7.0：底部菜单 5 → 4（移除「同步」，移动端改为相册自动备份），
/// 底部菜单：文件 / 笔记 / 传输任务 / 设置
class _AppScaffold extends StatelessWidget {
  final Widget child;

  const _AppScaffold({required this.child});

  /// 底部导航路由（顺序与 destinations 一一对应）
  static const List<String> _routes = [
    '/files',
    '/notes',
    '/transfer',
    AppRoutes.offlineDownload,
    '/album',
    '/settings',
  ];

  @override
  Widget build(BuildContext context) {
    // 获取当前路由对应的底部导航索引
    final location = GoRouterState.of(context).matchedLocation;
    // `/repair` 已并入传输页，仍需高亮「传输任务」而非回落到「文件」
    final normalized = location == '/repair' ? '/transfer' : location;
    final currentIndex = _routes.indexOf(normalized).clamp(0, _routes.length - 1);

    return Scaffold(
      body: child,
      bottomNavigationBar: NavigationBar(
        selectedIndex: currentIndex,
        onDestinationSelected: (index) => context.go(_routes[index]),
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.folder_outlined),
            selectedIcon: Icon(Icons.folder),
            label: '文件',
          ),
          NavigationDestination(
            icon: Icon(Icons.note_outlined),
            selectedIcon: Icon(Icons.note),
            label: '笔记',
          ),
          NavigationDestination(
            icon: Icon(Icons.cloud_download_outlined),
            selectedIcon: Icon(Icons.cloud_download),
            label: '传输任务',
          ),
          NavigationDestination(
            icon: Icon(Icons.cloud_download_outlined),
            selectedIcon: Icon(Icons.cloud_download),
            label: '离线下载',
          ),
          NavigationDestination(
            icon: Icon(Icons.photo_library_outlined),
            selectedIcon: Icon(Icons.photo_library),
            label: '相册同步',
          ),
          NavigationDestination(
            icon: Icon(Icons.settings_outlined),
            selectedIcon: Icon(Icons.settings),
            label: '设置',
          ),
        ],
      ),
    );
  }
}
