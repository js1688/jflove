import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'config/theme.dart';
import 'providers/session_provider.dart';
import 'pages/login/login_page.dart';
import 'pages/files/file_list_page.dart';
import 'pages/files/disk_browser_page.dart';
import 'pages/repair/repair_center_page.dart';
import 'pages/files/file_preview_page.dart';
import 'pages/notes/note_list_page.dart';
import 'pages/notes/note_edit_page.dart';
import 'pages/sync/sync_page.dart';
import 'pages/transfer/transfer_page.dart';
import 'pages/settings/settings_page.dart';
import 'pages/admin/admin_users_page.dart';
import 'pages/admin/admin_disks_page.dart';
import 'pages/admin/admin_permissions_page.dart';

/// JFLove 移动端 App
class JFLoveApp extends ConsumerWidget {
  const JFLoveApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final session = ref.watch(sessionManagerProvider);

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
            GoRoute(path: '/sync', builder: (_, _) => const SyncPage()),
            GoRoute(path: '/transfer', builder: (_, _) => const TransferPage()),
            // v1.4.2：修复中心（全平台共享任务列表）
            GoRoute(
              path: '/repair',
              builder: (_, _) => const RepairCenterPage(),
            ),
            GoRoute(path: '/settings', builder: (_, _) => const SettingsPage()),
          ],
        ),
        // 独立页面（不显示底部导航）
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
      ],
    );

    return MaterialApp.router(
      title: 'JFLove',
      theme: AppTheme.light,
      darkTheme: AppTheme.dark,
      themeMode: ThemeMode.system,
      routerConfig: router,
      debugShowCheckedModeBanner: false,
    );
  }
}

/// 底部导航框架
///
/// 对标桌面端 FluentWindow + NavigationInterface。
/// 底部菜单：文件 / 笔记 / 同步 / 传输任务 / 设置
class _AppScaffold extends StatelessWidget {
  final Widget child;

  const _AppScaffold({required this.child});

  @override
  Widget build(BuildContext context) {
    // 获取当前路由对应的底部导航索引
    final location = GoRouterState.of(context).matchedLocation;
    int currentIndex = 0;
    if (location == '/notes') currentIndex = 1;
    if (location == '/sync') currentIndex = 2;
    if (location == '/transfer') currentIndex = 3;
    if (location == '/repair') currentIndex = 4;
    if (location == '/settings') currentIndex = 5;

    return Scaffold(
      body: child,
      bottomNavigationBar: NavigationBar(
        selectedIndex: currentIndex,
        onDestinationSelected: (index) {
          final routes = [
            '/files',
            '/notes',
            '/sync',
            '/transfer',
            '/repair',
            '/settings',
          ];
          context.go(routes[index]);
        },
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
          NavigationDestination(icon: Icon(Icons.sync), label: '同步'),
          NavigationDestination(
            icon: Icon(Icons.cloud_download_outlined),
            selectedIcon: Icon(Icons.cloud_download),
            label: '传输任务',
          ),
          // v1.4.2：修复中心
          NavigationDestination(
            icon: Icon(Icons.healing_outlined),
            selectedIcon: Icon(Icons.healing),
            label: '修复中心',
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
