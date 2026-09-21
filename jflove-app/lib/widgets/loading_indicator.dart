import 'package:flutter/material.dart';

/// 居中加载动画
///
/// **v1.5.0 起已弃用**：设计规范要求加载态使用**骨架屏**而不是转圈动画 ——
/// 骨架屏能提前占住最终布局的高度，内容到达时不会发生跳动（转圈则会让
/// 页面先塌缩再撑开）。请改用 [ListSkeleton] / [SkeletonBox]：
///
/// ```dart
/// // 列表加载中
/// const ListSkeleton(rows: 5);
/// // 单块加载中（如图片、表单区块）
/// const SkeletonBox(width: double.infinity, height: 44);
/// ```
///
/// 目前 `lib/` 下已无任何引用（`MermaidBlock` 内部的「渲染中」占位是渲染
/// 引擎的专属状态，不属于页面加载态，故保留）。本文件仅为向后兼容保留，
/// **新代码请勿使用**。
@Deprecated('v1.5.0 起加载态改用骨架屏：ListSkeleton / SkeletonBox')
class LoadingIndicator extends StatelessWidget {
  final String? message;

  const LoadingIndicator({super.key, this.message});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const CircularProgressIndicator(),
          if (message != null) ...[
            const SizedBox(height: 16),
            Text(message!, style: Theme.of(context).textTheme.bodyMedium),
          ],
        ],
      ),
    );
  }
}
