import 'package:flutter/material.dart';

import '../config/design_tokens.dart';

/// 统一卡片（需求 AC-6）
///
/// v1.4.2 各页面直接 `Card(...)`（Material 默认 elevation 浮起 + 默认圆角），
/// 导致同屏卡片阴影/圆角不一致。这里统一为「描边 + 极浅阴影」，与 Web / 桌面端一致。
class AppCard extends StatelessWidget {
  const AppCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(16),
    this.margin,
    this.onTap,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final EdgeInsetsGeometry? margin;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    final content = Padding(padding: padding, child: child);
    return Container(
      margin: margin,
      decoration: BoxDecoration(
        color: t.bgSurface,
        borderRadius: BorderRadius.circular(t.rLg),
        border: Border.all(color: t.borderSubtle),
        boxShadow: t.e1,
      ),
      clipBehavior: Clip.antiAlias,
      child: onTap == null
          ? content
          : InkWell(onTap: onTap, child: content),
    );
  }
}

/// 卡片内的小节标题
class SectionTitle extends StatelessWidget {
  const SectionTitle(this.text, {super.key, this.trailing});

  final String text;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    return Row(
      children: [
        Expanded(
          child: Text(
            text,
            style: TextStyle(
              fontSize: 15,
              fontWeight: FontWeight.w600,
              color: t.fgDefault,
            ),
          ),
        ),
        ?trailing,
      ],
    );
  }
}

/// 统计卡（概览层，需求 AC-7）
///
/// **当前无页面使用**：v1.5.0 修补时按用户反馈删除了同步页的
/// 「配置总数 / 已启用 / 自动同步」概览（"文档管理上面一堆小卡片都不需要"），
/// 至此 `lib/` 下已无引用。保留为公共组件，供后续需要概览层的页面复用。
class StatCard extends StatelessWidget {
  const StatCard({
    super.key,
    required this.icon,
    required this.value,
    required this.label,
    this.tone = StatTone.brand,
  });

  final IconData icon;
  final String value;
  final String label;
  final StatTone tone;

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    final (bg, fg) = tone.colors(t);
    return AppCard(
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 34,
            height: 34,
            decoration: BoxDecoration(
              color: bg,
              borderRadius: BorderRadius.circular(t.rMd),
            ),
            child: Icon(icon, size: 18, color: fg),
          ),
          const SizedBox(height: 10),
          Text(
            value,
            style: TextStyle(
              fontSize: 20,
              fontWeight: FontWeight.w700,
              color: t.fgDefault,
              letterSpacing: -0.3,
            ),
          ),
          const SizedBox(height: 2),
          Text(label, style: TextStyle(fontSize: 11.5, color: t.fgMuted)),
        ],
      ),
    );
  }
}

/// 统计卡色调
enum StatTone {
  brand,
  sky,
  mint,
  rose;

  (Color, Color) colors(AppTokens t) {
    switch (this) {
      case StatTone.brand:
        return (t.bgActive, AppTokens.brand600);
      case StatTone.sky:
        return (
          AppTokens.info500.withValues(alpha: 0.14),
          AppTokens.info500,
        );
      case StatTone.mint:
        return (
          AppTokens.success500.withValues(alpha: 0.14),
          AppTokens.success500,
        );
      case StatTone.rose:
        return (
          AppTokens.accent500.withValues(alpha: 0.14),
          AppTokens.accent500,
        );
    }
  }
}

/// 状态徽标（替代各页面手写的彩色文字）
class AppBadge extends StatelessWidget {
  const AppBadge(this.text, {super.key, this.tone = BadgeTone.neutral, this.icon});

  final String text;
  final BadgeTone tone;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    final (bg, fg) = tone.colors(t);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (icon != null) ...[
            Icon(icon, size: 11, color: fg),
            const SizedBox(width: 4),
          ],
          Text(
            text,
            style: TextStyle(
              fontSize: 11,
              fontWeight: FontWeight.w600,
              color: fg,
            ),
          ),
        ],
      ),
    );
  }
}

/// 徽标色调
enum BadgeTone {
  brand,
  success,
  warning,
  danger,
  neutral;

  (Color, Color) colors(AppTokens t) {
    switch (this) {
      case BadgeTone.brand:
        return (t.bgActive, AppTokens.brand600);
      case BadgeTone.success:
        return (
          AppTokens.success500.withValues(alpha: 0.14),
          AppTokens.success500,
        );
      case BadgeTone.warning:
        return (
          AppTokens.warning500.withValues(alpha: 0.16),
          AppTokens.warning500,
        );
      case BadgeTone.danger:
        return (
          AppTokens.danger500.withValues(alpha: 0.14),
          AppTokens.danger500,
        );
      case BadgeTone.neutral:
        return (t.bgSunken, t.fgMuted);
    }
  }
}

/// 骨架屏（替代转圈，避免加载时布局跳动）
class SkeletonBox extends StatelessWidget {
  const SkeletonBox({
    super.key,
    this.width,
    required this.height,
    this.radius = 6,
  });

  final double? width;
  final double height;
  final double radius;

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    return Container(
      width: width,
      height: height,
      decoration: BoxDecoration(
        color: t.bgSunken,
        borderRadius: BorderRadius.circular(radius),
      ),
    );
  }
}

/// 列表骨架：若干行「图标块 + 两条文字条」
class ListSkeleton extends StatelessWidget {
  const ListSkeleton({super.key, this.rows = 5});

  final int rows;

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    return ListView.builder(
      padding: const EdgeInsets.all(16),
      itemCount: rows,
      itemBuilder: (_, _) => Padding(
        padding: const EdgeInsets.only(bottom: 18),
        child: Row(
          children: [
            const SkeletonBox(width: 36, height: 36, radius: 8),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const SkeletonBox(width: 140, height: 12),
                  const SizedBox(height: 8),
                  SkeletonBox(width: 90, height: 10, radius: 5),
                ],
              ),
            ),
            Icon(Icons.chevron_right_rounded, color: t.borderDefault),
          ],
        ),
      ),
    );
  }
}

/// 主操作按钮（品牌渐变）
class PrimaryGradientButton extends StatelessWidget {
  const PrimaryGradientButton({
    super.key,
    required this.label,
    this.icon,
    this.onPressed,
    this.loading = false,
    this.expand = false,
  });

  final String label;
  final IconData? icon;
  final VoidCallback? onPressed;
  final bool loading;
  final bool expand;

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    final enabled = onPressed != null && !loading;
    return Opacity(
      opacity: enabled ? 1 : 0.5,
      child: Container(
        decoration: BoxDecoration(
          gradient: t.gradBrand,
          borderRadius: BorderRadius.circular(t.rMd),
          boxShadow: enabled ? t.e2 : null,
        ),
        child: Material(
          color: Colors.transparent,
          child: InkWell(
            onTap: enabled ? onPressed : null,
            borderRadius: BorderRadius.circular(t.rMd),
            child: Padding(
              padding: EdgeInsets.symmetric(
                horizontal: expand ? 16 : 20,
                vertical: 13,
              ),
              child: Row(
                mainAxisSize: expand ? MainAxisSize.max : MainAxisSize.min,
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  if (loading)
                    const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(
                        strokeWidth: 2,
                        color: Colors.white,
                      ),
                    )
                  else if (icon != null)
                    Icon(icon, size: 18, color: Colors.white),
                  if (loading || icon != null) const SizedBox(width: 8),
                  Text(
                    label,
                    style: const TextStyle(
                      fontSize: 14,
                      fontWeight: FontWeight.w600,
                      color: Colors.white,
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
