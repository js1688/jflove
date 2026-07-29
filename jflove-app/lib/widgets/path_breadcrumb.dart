import 'package:flutter/material.dart';

/// 路径面包屑导航
///
/// 集成在 AppBar bottom 中，紧凑单行显示当前路径。
class PathBreadcrumb extends StatelessWidget {
  final String currentPath;
  final VoidCallback? onBack;

  const PathBreadcrumb({super.key, required this.currentPath, this.onBack});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      child: Row(
        children: [
          if (onBack != null)
            GestureDetector(
              onTap: onBack,
              child: Icon(
                Icons.arrow_back,
                size: 18,
                color: theme.colorScheme.primary,
              ),
            ),
          const SizedBox(width: 4),
          Expanded(
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Text(
                currentPath.isEmpty ? '/' : '/$currentPath',
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
