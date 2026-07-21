import 'package:flutter/material.dart';

/// 路径面包屑导航
///
/// 对标桌面端 _path_label + _back_btn。
class PathBreadcrumb extends StatelessWidget {
  final String currentPath;
  final VoidCallback? onBack;

  const PathBreadcrumb({super.key, required this.currentPath, this.onBack});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      decoration: BoxDecoration(
        color: Theme.of(
          context,
        ).colorScheme.surfaceContainerHighest.withAlpha(80),
        border: Border(
          bottom: BorderSide(color: Theme.of(context).dividerColor),
        ),
      ),
      child: Row(
        children: [
          if (onBack != null && currentPath.isNotEmpty)
            IconButton(
              icon: const Icon(Icons.arrow_back),
              tooltip: '返回上级',
              onPressed: onBack,
              visualDensity: VisualDensity.compact,
            ),
          Expanded(
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Row(
                children: [
                  const Icon(Icons.home, size: 16),
                  ..._buildSegments(),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  List<Widget> _buildSegments() {
    if (currentPath.isEmpty) {
      return [const Text(' / ', style: TextStyle(fontSize: 14))];
    }

    final segments = currentPath.split('/').where((s) => s.isNotEmpty).toList();
    final widgets = <Widget>[];
    String builtPath = '';

    for (final seg in segments) {
      builtPath = builtPath.isEmpty ? seg : '$builtPath/$seg';
      widgets.add(
        Text(
          ' / ',
          style: TextStyle(color: Colors.grey.shade500, fontSize: 14),
        ),
      );
      widgets.add(
        GestureDetector(
          onTap: () {
            // TODO: 跳转到指定路径
          },
          child: Text(seg, style: const TextStyle(fontSize: 14)),
        ),
      );
    }
    return widgets;
  }
}
