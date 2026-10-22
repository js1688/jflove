import '../../utils/exception.dart';

/// 离线下载错误码 → 中文原因 / 建议（v1.8.1 / F29、AC-250）
///
/// 口径与桌面端 `offline-utils.ts` 的 `ERROR_SUGGESTIONS`、
/// Web 端 `utils/offline-download.ts` 保持一致；错误码来自服务端
/// v1.8.0 设计 §2.2.9 的枚举。
/// 失败类型的下一步建议（空串表示无建议）
const Map<String, String> _suggestions = <String, String>{
  'unsupported_site': '换一个地址，或把地址反馈给管理员。',
  'no_media': '确认页面确实含媒体；若页面靠 JS 注入，请联系管理员开启深度分析。',
  'deep_analyze_disabled': '请联系管理员开启深度分析后重试。',
  'login_required': '本功能不支持登录态站点，请换一个公开地址。',
  'drm_protected': '受版权保护的内容无法下载。',
  'url_expired': '请重新分析该地址后再试。',
  'referer_denied': '可稍后重试；若持续失败，请换一个地址。',
  'net_timeout': '可重试；若持续超时，请检查服务端网络。',
  'ssrf_blocked': '请使用公网 http(s) 地址。',
  'disk_full': '清理目标磁盘空间后重试。',
  'too_large': '换更低的清晰度，或联系管理员调整体积上限。',
  'target_not_writable': '换一个目标目录后重试。',
  'target_not_configured': '先在「离线下载 → 设置」中配置默认目录。',
  'config_invalid': '默认目录已失效，请重新配置。',
  'no_writable_disk': '请联系管理员为你的账号分配可写磁盘。',
  'parser_outdated': '请等待服务端升级解析组件。',
  'restart_interrupted': '可直接重试，已完成的分片不会重下。',
  'canceled': '可点击重试继续下载。',
  'page_limit': '已达抓取页数上限，不能继续追加。',
  'invalid_state': '当前状态不允许该操作，请刷新后重试。',
  'not_found': '任务不存在或已被删除。',
};

/// 该错误码是否允许「重试」
///
/// 与服务端 `retry_task` 的拒绝清单同口径：DRM / 需登录 / 超限 / 磁盘满 /
/// 无写权限 / 目录失效 一律不可重试。
bool canRetryErrorCode(String errorCode) {
  const blocked = <String>{
    'drm_protected',
    'login_required',
    'too_large',
    'disk_full',
    'no_writable_disk',
    'target_not_configured',
    'config_invalid',
  };
  return !blocked.contains(errorCode.trim());
}

/// 失败建议（无对应码时返回空串）
String errorSuggestion(String errorCode) =>
    _suggestions[errorCode.trim()] ?? '';

/// 服务端错误码 → 用户可见中文（含建议）
String offlineErrorText(String errorCode, String errorMessage) {
  final message = errorMessage.trim();
  final suggestion = errorSuggestion(errorCode);
  final base = message.isNotEmpty ? message : '离线下载失败';
  return suggestion.isEmpty ? base : '$base（建议：$suggestion）';
}

/// 任意异常 → 用户可见中文（不展示 Dio 原始英文）
String offlineOperationError(Object error, {String fallback = '操作失败，请稍后重试'}) =>
    userFacingError(error, fallback: fallback);
