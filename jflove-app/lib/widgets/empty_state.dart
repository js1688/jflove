import 'package:flutter/material.dart';

import '../config/design_tokens.dart';

/// 空状态引导（v1.5.0 接入设计令牌）
///
/// 图标底色从 `Theme.of(context).disabledColor` 换成统一的「下沉面 + 淡边框」
/// 圆形底盘，与 Web / 桌面端的 EmptyState 视觉一致。
class EmptyState extends StatelessWidget {
  final IconData icon;
  final String title;
  final String? subtitle;
  final Widget? action;

  const EmptyState({
    super.key,
    this.icon = Icons.inbox_outlined,
    required this.title,
    this.subtitle,
    this.action,
  });

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 72,
              height: 72,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: t.bgSunken,
                border: Border.all(color: t.borderSubtle),
              ),
              child: Icon(icon, size: 32, color: t.fgSubtle),
            ),
            SizedBox(height: t.s4),
            Text(
              title,
              style: TextStyle(
                fontSize: 15,
                fontWeight: FontWeight.w600,
                color: t.fgDefault,
              ),
              textAlign: TextAlign.center,
            ),
            if (subtitle != null) ...[
              SizedBox(height: t.s2),
              Text(
                subtitle!,
                style: TextStyle(fontSize: 13, height: 1.6, color: t.fgMuted),
                textAlign: TextAlign.center,
              ),
            ],
            if (action != null) ...[SizedBox(height: t.s6), action!],
          ],
        ),
      ),
    );
  }
}
