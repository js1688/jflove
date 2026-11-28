/// 容量 / 文件大小的统一格式化（v1.10.26，R1）
///
/// 背景：`transfer_page.dart` / `repair_center_page.dart` / `remote_terminal_page.dart`
/// 各写了一份 `_formatSize`，单位阈值略有差异，且**都不处理"取不到"的情况**
/// —— 服务端下发 null 时会被当成 0 显示成 `0 B`，误导用户（R1 要求渲染 `—`）。
///
/// 本文件是三端统一的格式化口径：
///   - Web / 桌面：`jflove-web/src/utils/format.ts::formatSize`
///     （B / KB / MB / GB / TB，1024 进制，保留 1 位小数）
///   - 移动端：本文件 `formatSize`（同上，**逐档与前端保持一致的字符串形态**）
library;

/// 服务端「取不到」的统一展示字符（R1 验收判据：不可读兜底 `—`，不崩）
const String kDash = '—';

/// 把字节数格式化为人类可读字符串：`1.5 KB` / `243.0 GB` / `952.0 GB`
///
/// 与 Web 端 `formatSize` 的口径一致：
///   - `< 1024` → `N B`（整数，不加小数）
///   - 其余按 1024 进制逐档，保留 1 位小数，最大到 TB
///
/// @param bytes 字节数；传 null 表示"服务端取不到" → 返回 [kDash]
/// @returns 形如 `243.0 GB`；取不到/非法值返回 `—`
String formatSize(int? bytes) {
  if (bytes == null) return kDash;
  if (bytes < 0) return kDash;
  if (bytes < 1024) return '$bytes B';
  const units = <String>['KB', 'MB', 'GB', 'TB'];
  var v = bytes / 1024;
  var i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return '${v.toStringAsFixed(1)} ${units[i]}';
}

/// 字符串型「取不到」兜底：空串 / null / 纯空白 → `—`
///
/// 用于 `disk_type` 这类文本字段（如 `btrfs` / `ntfs`）。
String orDash(String? value) {
  final s = (value ?? '').trim();
  return s.isEmpty ? kDash : s;
}
