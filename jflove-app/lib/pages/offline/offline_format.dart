/// 离线下载页面的展示格式化（无网络、无状态）
///
/// 任务中心、新建任务页、详情弹层共用，避免三处各写一份口径。
library;

/// 字节数格式化（1024 进制，与传输 / 修复页一致）
String formatSize(int bytes) {
  if (bytes < 1024) return '$bytes B';
  if (bytes < 1024 * 1024) {
    return '${(bytes / 1024).toStringAsFixed(1)} KB';
  }
  if (bytes < 1024 * 1024 * 1024) {
    return '${(bytes / 1024 / 1024).toStringAsFixed(1)} MB';
  }
  return '${(bytes / 1024 / 1024 / 1024).toStringAsFixed(2)} GB';
}

/// 时长格式化（毫秒 → `m:ss` / `h:mm:ss`）
String formatDuration(int milliseconds) {
  final totalSeconds = milliseconds ~/ 1000;
  final hours = totalSeconds ~/ 3600;
  final minutes = (totalSeconds % 3600) ~/ 60;
  final seconds = totalSeconds % 60;
  final mm = minutes.toString().padLeft(2, '0');
  final ss = seconds.toString().padLeft(2, '0');
  return hours > 0 ? '$hours:$mm:$ss' : '$minutes:$ss';
}

/// ISO8601 字符串 → 本地时间 `yyyy-MM-dd HH:mm:ss`
///
/// 服务端下发 UTC ISO8601；解析失败时原样返回，不抛异常。
String? formatDateTime(String? iso) {
  final value = iso?.trim() ?? '';
  if (value.isEmpty) return null;
  final parsed = DateTime.tryParse(value);
  if (parsed == null) return value;
  final local = parsed.toLocal().toString();
  return local.length >= 19 ? local.substring(0, 19) : local;
}
