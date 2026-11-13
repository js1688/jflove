import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jflove_app/config/theme.dart';
import 'package:jflove_app/pages/remote/remote_list_page.dart';
import 'package:jflove_app/providers/remote_provider.dart';
import 'package:jflove_app/services/remote_ssh_service.dart';

/// 远程连接列表页渲染（v1.10.0）
///
/// 不连服务端：用 override 注入一份固定的分组 + 连接，验证页面能渲染出
/// 标题、分组 chip 与连接卡片（防止「新页面在真实数据下空白」）。
void main() {
  testWidgets('渲染分组、连接卡片与跳板标记', (tester) async {
    final overview = RemoteOverview(
      groups: const [
        RemoteGroup(id: 1, name: '生产环境', sortOrder: 0, connectionCount: 1),
      ],
      connections: [
        RemoteConnection.fromJson({
          'id': 1,
          'ssh_group_id': 1,
          'name': 'web-01',
          'host': '192.168.1.21',
          'port': 22,
          'username': 'tanjun',
          'auth_type': 'password',
          'jump_connection_id': 0,
          'has_password': true,
          'has_private_key': false,
        }),
        RemoteConnection.fromJson({
          'id': 2,
          'ssh_group_id': 1,
          'name': 'db-01',
          'host': '10.0.3.15',
          'port': 2222,
          'username': 'tanjun',
          'auth_type': 'publickey',
          'jump_connection_id': 1,
          'has_password': false,
          'has_private_key': true,
        }),
      ],
    );

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          remoteOverviewProvider.overrideWith((ref) async => overview),
        ],
        child: MaterialApp(
          theme: AppTheme.light,
          home: const RemoteListPage(),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('远程连接'), findsOneWidget);
    expect(find.text('生产环境 1'), findsOneWidget);
    expect(find.text('web-01'), findsOneWidget);
    expect(find.text('db-01'), findsOneWidget);
    expect(find.text('↳ 跳板 web-01'), findsOneWidget);
    expect(find.text('密钥'), findsOneWidget);
  });

  testWidgets('无连接时显示空状态', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          remoteOverviewProvider.overrideWith(
            (ref) async => const RemoteOverview(groups: [], connections: []),
          ),
        ],
        child: MaterialApp(
          theme: AppTheme.light,
          home: const RemoteListPage(),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('还没有连接'), findsOneWidget);
  });
}
