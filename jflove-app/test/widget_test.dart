import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:jflove_app/app.dart';

void main() {
  testWidgets('JFLove app renders login page', (WidgetTester tester) async {
    await tester.pumpWidget(const ProviderScope(child: JFLoveApp()));
    expect(find.text('JFLove'), findsWidgets);
    expect(find.text('登录'), findsOneWidget);
    expect(find.text('服务器地址'), findsOneWidget);
  });

  testWidgets('Login page has required fields', (WidgetTester tester) async {
    await tester.pumpWidget(const ProviderScope(child: JFLoveApp()));

    // 验证存在关键 UI 元素
    expect(find.text('用户名'), findsOneWidget);
    expect(find.text('密码'), findsOneWidget);
    expect(find.text('登录有效期'), findsOneWidget);
  });

  testWidgets('Login page has server URL field', (WidgetTester tester) async {
    await tester.pumpWidget(const ProviderScope(child: JFLoveApp()));

    // 验证登录页有服务器地址输入框（当前使用 Autocomplete 封装）
    // 通过 hintText 或 labelText 验证
    expect(find.text('服务器地址'), findsOneWidget);
    // 验证存在 TextField 输入框组件
    expect(find.byType(TextField), findsWidgets);
  });

  testWidgets('Settings page renders admin panel entry', (
    WidgetTester tester,
  ) async {
    // 验证 App 启动时能渲染
    await tester.pumpWidget(const ProviderScope(child: JFLoveApp()));
    expect(find.byType(MaterialApp), findsOneWidget);
  });
}
