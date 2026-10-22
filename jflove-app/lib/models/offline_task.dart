/// 离线下载只读数据模型（v1.8.0）
///
/// 字段对齐 `文档记录/设计文档/v1.8.0.md`：
///   - §2.2.2 `GET /offline/tasks` → `TaskRow`；
///   - §2.2.3 `GET /offline/detail` → `{task, items, has_more_images}` 与 `ItemRow`；
///   - §2.2.7 `GET /offline/meta` → 客户端初始化元信息。
///
/// 移动端**只读**：本文件只有展示字段与展示用派生属性，
/// **不定义**任何创建/勾选/操作动作，避免被误解为可写模型。
///
/// 容错约定（全部 `fromJson`）：
///   - 字段缺失 → 默认值（数值 0 / 字符串 '' / 布尔 false）；
///   - 类型不符（如数值给了字符串）→ 能转则转，不能转回落默认值；
///   - 字段为 `null` → 回落默认值；
///   任何情况下都**不抛异常**，页面最差只显示"信息不完整"，不会白屏。
library;

// ── JSON 取值助手（全部容错，不抛异常）────────────────────────────

int _asInt(Object? value) {
  if (value is int) return value;
  if (value is num) return value.toInt();
  if (value is String) return int.tryParse(value) ?? 0;
  if (value is bool) return value ? 1 : 0;
  return 0;
}

String _asString(Object? value) {
  if (value == null) return '';
  if (value is String) return value;
  return value.toString();
}

/// 可空字符串：空串 / 非字符串一律回落 `null`
String? _asStringOrNull(Object? value) {
  if (value is String && value.trim().isNotEmpty) return value;
  return null;
}

bool _asBool(Object? value) {
  if (value is bool) return value;
  if (value is num) return value != 0;
  if (value is String) {
    final normalized = value.toLowerCase();
    return normalized == 'true' || normalized == '1';
  }
  return false;
}

Map<String, dynamic> _asMap(Object? value) {
  if (value is Map<String, dynamic>) return value;
  if (value is Map) return Map<String, dynamic>.from(value);
  return const <String, dynamic>{};
}

/// 解析对象数组（元素不是 Map 时跳过，不抛异常）
List<Map<String, dynamic>> _asMapList(Object? value) {
  if (value is! List) return const <Map<String, dynamic>>[];
  return value.whereType<Map>().map(_asMap).toList(growable: false);
}

double _clamp01(double value) {
  if (value.isNaN || value <= 0) return 0;
  if (value >= 1) return 1;
  return value;
}

// ── 任务 ─────────────────────────────────────────────────────────

/// 离线下载任务（只读，对应服务端 `TaskRow`）
class OfflineTask {
  const OfflineTask({
    this.id = 0,
    this.username = '',
    this.sourceDomain = '',
    this.pageTitle = '',
    this.status = '',
    this.statusText = '',
    this.itemTotal = 0,
    this.itemDone = 0,
    this.bytesTotal = 0,
    this.bytesDone = 0,
    this.speedBps = 0,
    this.targetDiskName = '',
    this.targetRelPath = '',
    this.errorCode = '',
    this.errorMessage = '',
    this.createdAt = '',
    this.startedAt,
    this.finishedAt,
    this.canOperate = false,
  });

  /// 任务 ID（服务端自增主键；只读页仅用于取详情）
  final int id;

  /// 创建者用户名（服务端按账号隔离，本账号看到的就是自己的）
  final String username;

  /// 来源域名（**脱敏展示**：只给域名，不给完整 URL）
  final String sourceDomain;

  /// 页面标题（服务端产物命名的依据；移动端展示为「产物名」）
  final String pageTitle;

  /// 状态机英文值：queued / analyzing / ready / downloading / merging /
  /// success / failed / canceled
  final String status;

  /// 状态中文文案（服务端下发，客户端不自己翻译，避免口径漂移）
  final String statusText;

  /// 清单总项数
  final int itemTotal;

  /// 已完成项数
  final int itemDone;

  /// 本次下载总字节（服务端按已选项 `est_size` 之和估算）
  final int bytesTotal;

  /// 已下载字节
  final int bytesDone;

  /// 实时速度（B/s，服务端 EMA 平滑）
  final int speedBps;

  /// 目标磁盘名
  final String targetDiskName;

  /// 目标目录（虚拟磁盘内的相对路径）
  final String targetRelPath;

  /// 业务错误码（§2.2.9 枚举）
  final String errorCode;

  /// 业务错误中文原因
  final String errorMessage;

  /// 创建时间（服务端 UTC ISO8601 字符串）
  final String createdAt;

  /// 开始时间（未开始为 null）
  final String? startedAt;

  /// 完成时间（未结束为 null）
  final String? finishedAt;

  /// 服务端判定「是本人任务且对目标磁盘仍有写权限」；
  /// 移动端只读页**不据此渲染任何操作**，仅保留字段以免裁剪响应时丢信息。
  final bool canOperate;

  /// 容错解析（缺字段/类型不符/null 都不崩）
  factory OfflineTask.fromJson(Map<String, dynamic>? json) {
    final map = json ?? const <String, dynamic>{};
    return OfflineTask(
      id: _asInt(map['id']),
      username: _asString(map['username']),
      sourceDomain: _asString(map['source_domain']),
      pageTitle: _asString(map['page_title']),
      status: _asString(map['status']),
      statusText: _asString(map['status_text']),
      itemTotal: _asInt(map['item_total']),
      itemDone: _asInt(map['item_done']),
      bytesTotal: _asInt(map['bytes_total']),
      bytesDone: _asInt(map['bytes_done']),
      speedBps: _asInt(map['speed_bps']),
      targetDiskName: _asString(map['target_disk_name']),
      targetRelPath: _asString(map['target_rel_path']),
      errorCode: _asString(map['error_code']),
      errorMessage: _asString(map['error_message']),
      createdAt: _asString(map['created_at']),
      startedAt: _asStringOrNull(map['started_at']),
      finishedAt: _asStringOrNull(map['finished_at']),
      canOperate: _asBool(map['can_operate']),
    );
  }

  /// 产物名：优先页面标题（产物命名依据），退化到来源域名
  String get productName {
    final title = pageTitle.trim();
    if (title.isNotEmpty) return title;
    final domain = sourceDomain.trim();
    if (domain.isNotEmpty) return domain;
    return '未命名产物';
  }

  /// 状态徽标文案：服务端文案优先，缺失时退化到英文状态值
  String get statusLabel {
    final text = statusText.trim();
    return text.isNotEmpty ? text : status;
  }

  /// 目标展示：`磁盘名/相对路径`（无目标时给占位符）
  String get targetDisplay {
    final disk = targetDiskName.trim();
    final rel = targetRelPath.trim().replaceAll(RegExp(r'^/+|/+$'), '');
    if (disk.isEmpty && rel.isEmpty) return '—';
    final path = rel.isEmpty ? '/' : '/$rel';
    return disk.isEmpty ? path : '$disk$path';
  }

  /// 是否为终态（终态任务不会再变化）
  bool get isTerminal =>
      status == 'success' || status == 'failed' || status == 'canceled';

  /// 是否处于下载/合并阶段（用于展示速度与进度条）
  bool get isTransferring => status == 'downloading' || status == 'merging';

  /// 进度 0.0 ~ 1.0：优先字节口径，其次项数口径（§2.4.4 双口径）
  double get progress {
    if (status == 'success') return 1;
    if (bytesTotal > 0) return _clamp01(bytesDone / bytesTotal);
    if (itemTotal > 0) return _clamp01(itemDone / itemTotal);
    return 0;
  }

  /// 进度百分比（0 ~ 100 整数）
  int get progressPercent => (progress * 100).round();

  /// 是否有可展示的失败原因
  bool get hasError => errorMessage.trim().isNotEmpty;
}

// ── 清单项 ───────────────────────────────────────────────────────

/// 离线下载清单项（只读，对应服务端 `ItemRow`）
class OfflineItem {
  const OfflineItem({
    this.id = 0,
    this.kind = '',
    this.displayName = '',
    this.ext = '',
    this.width = 0,
    this.height = 0,
    this.durationMs = 0,
    this.estSize = 0,
    this.isEstimated = false,
    this.isSelected = false,
    this.status = '',
    this.errorMessage = '',
  });

  final int id;

  /// 类型：video / audio / image / subtitle
  final String kind;

  /// 展示名（服务端清洗后的文件名主干）
  final String displayName;

  /// 扩展名（不含点）
  final String ext;

  final int width;
  final int height;

  /// 时长（毫秒；图片/字幕为 0）
  final int durationMs;

  /// 预估/实际大小（字节）
  final int estSize;

  /// 大小是否为估算值
  final bool isEstimated;

  /// 当时是否被勾选下载（只读展示）
  final bool isSelected;

  /// 单项状态（pending / downloading / success / skipped / failed ...）
  final String status;

  /// 单项失败原因
  final String errorMessage;

  /// 容错解析（缺字段/类型不符/null 都不崩）
  factory OfflineItem.fromJson(Map<String, dynamic>? json) {
    final map = json ?? const <String, dynamic>{};
    return OfflineItem(
      id: _asInt(map['id']),
      kind: _asString(map['kind']),
      displayName: _asString(map['display_name']),
      ext: _asString(map['ext']),
      width: _asInt(map['width']),
      height: _asInt(map['height']),
      durationMs: _asInt(map['duration_ms']),
      estSize: _asInt(map['est_size']),
      isEstimated: _asBool(map['is_estimated']),
      isSelected: _asBool(map['is_selected']),
      status: _asString(map['status']),
      errorMessage: _asString(map['error_message']),
    );
  }

  /// 分辨率文案（宽高缺一不可，否则返回空串）
  String get resolutionText =>
      width > 0 && height > 0 ? '${width}x$height' : '';

  /// 名称兜底
  String get title => displayName.trim().isEmpty ? '未命名项' : displayName;
}

// ── 列表 / 详情 / 元信息响应包装 ──────────────────────────────────

/// `GET /offline/tasks` 响应
class OfflineTaskList {
  const OfflineTaskList({this.total = 0, this.tasks = const []});

  /// 当前账号任务总数（与分页无关）
  final int total;

  final List<OfflineTask> tasks;

  factory OfflineTaskList.fromJson(Map<String, dynamic>? json) {
    final map = json ?? const <String, dynamic>{};
    return OfflineTaskList(
      total: _asInt(map['total']),
      tasks: _asMapList(
        map['tasks'],
      ).map(OfflineTask.fromJson).toList(growable: false),
    );
  }
}

/// `GET /offline/detail` 响应
class OfflineTaskDetail {
  const OfflineTaskDetail({
    this.task = const OfflineTask(),
    this.items = const [],
    this.hasMoreImages = false,
  });

  final OfflineTask task;
  final List<OfflineItem> items;

  /// 服务端是否还有未抓取的图片（只读页只提示，不提供追加操作）
  final bool hasMoreImages;

  factory OfflineTaskDetail.fromJson(Map<String, dynamic>? json) {
    final map = json ?? const <String, dynamic>{};
    return OfflineTaskDetail(
      task: OfflineTask.fromJson(_asMap(map['task'])),
      items: _asMapList(
        map['items'],
      ).map(OfflineItem.fromJson).toList(growable: false),
      hasMoreImages: _asBool(map['has_more_images']),
    );
  }
}

/// 可写虚拟磁盘（`GET /offline/meta` 的 `writable_disks` 元素）
class OfflineWritableDisk {
  const OfflineWritableDisk({this.diskId = 0, this.name = ''});

  final int diskId;
  final String name;

  factory OfflineWritableDisk.fromJson(Map<String, dynamic>? json) {
    final map = json ?? const <String, dynamic>{};
    return OfflineWritableDisk(
      diskId: _asInt(map['disk_id']),
      name: _asString(map['name']),
    );
  }
}

/// `GET /offline/meta` 响应（移动端只用到「是否可用」与可写磁盘）
class OfflineMeta {
  const OfflineMeta({
    this.enabled = true,
    this.canUse = true,
    this.configured = false,
    this.unusableReason = '',
    this.configDiskId = 0,
    this.configRelPath = '',
    this.configValid = false,
    this.writableDisks = const [],
    this.maxItems = 0,
    this.maxFileSize = 0,
    this.maxImages = 0,
    this.pollIntervalMs = 2500,
  });

  /// 总开关
  final bool enabled;

  /// 总开关开 **且** 账号至少有一个可写磁盘
  final bool canUse;

  /// 是否已有有效默认目录
  final bool configured;

  /// 不可用原因：disabled / no_writable_disk / not_configured / config_invalid
  final String unusableReason;

  /// 默认目录所在磁盘 ID（0 表示未配置）
  final int configDiskId;

  /// 默认目录相对路径
  final String configRelPath;

  /// 默认目录当前是否仍然有效
  final bool configValid;

  /// 当前账号有 write 权限的磁盘（服务端已过滤）
  final List<OfflineWritableDisk> writableDisks;

  final int maxItems;
  final int maxFileSize;
  final int maxImages;

  /// 服务端建议轮询间隔（移动端固定 2.5s，仅记录不驱动）
  final int pollIntervalMs;

  /// 默认值：字段全缺时按「可用」处理，避免把网络抖动误报成"功能关闭"
  static const OfflineMeta empty = OfflineMeta();

  factory OfflineMeta.fromJson(Map<String, dynamic>? json) {
    final map = json ?? const <String, dynamic>{};
    final config = _asMap(map['config']);
    return OfflineMeta(
      enabled: _asBool(map['enabled']),
      canUse: _asBool(map['can_use']),
      configured: _asBool(map['configured']),
      unusableReason: _asString(map['unusable_reason']),
      configDiskId: _asInt(config['disk_id']),
      configRelPath: _asString(config['rel_path']),
      configValid: _asBool(config['valid']),
      writableDisks: _asMapList(
        map['writable_disks'],
      ).map(OfflineWritableDisk.fromJson).toList(growable: false),
      maxItems: _asInt(map['max_items']),
      maxFileSize: _asInt(map['max_file_size']),
      maxImages: _asInt(map['max_images']),
      pollIntervalMs: _asInt(map['poll_interval_ms']),
    );
  }
}

// ── 写接口响应模型（v1.8.1：移动端从只读补成完整功能）──────────────

/// `POST /offline/analyze` 响应
class OfflineAnalyzeResult {
  const OfflineAnalyzeResult({
    this.taskId = 0,
    this.status = '',
    this.sourceDomain = '',
    this.pageTitle = '',
  });

  final int taskId;
  final String status;
  final String sourceDomain;
  final String pageTitle;

  factory OfflineAnalyzeResult.fromJson(Map<String, dynamic>? json) {
    final map = json ?? const <String, dynamic>{};
    return OfflineAnalyzeResult(
      taskId: _asInt(map['task_id']),
      status: _asString(map['status']),
      sourceDomain: _asString(map['source_domain']),
      pageTitle: _asString(map['page_title']),
    );
  }
}

/// `POST /offline/select` 响应
class OfflineSelectResult {
  const OfflineSelectResult({
    this.taskId = 0,
    this.status = '',
    this.selected = 0,
    this.bytesTotal = 0,
  });

  final int taskId;
  final String status;

  /// 本次实际勾选并进入下载队列的项数
  final int selected;

  /// 本次下载总字节（服务端按已选项 est_size 估算）
  final int bytesTotal;

  factory OfflineSelectResult.fromJson(Map<String, dynamic>? json) {
    final map = json ?? const <String, dynamic>{};
    return OfflineSelectResult(
      taskId: _asInt(map['task_id']),
      status: _asString(map['status']),
      selected: _asInt(map['selected']),
      bytesTotal: _asInt(map['bytes_total']),
    );
  }
}

/// `POST /offline/next-page` 响应（图片追加下一页）
class OfflineNextPageResult {
  const OfflineNextPageResult({
    this.added = 0,
    this.itemTotal = 0,
    this.hasMore = false,
    this.pageIndex = 0,
  });

  final int added;
  final int itemTotal;
  final bool hasMore;
  final int pageIndex;

  factory OfflineNextPageResult.fromJson(Map<String, dynamic>? json) {
    final map = json ?? const <String, dynamic>{};
    return OfflineNextPageResult(
      added: _asInt(map['added']),
      itemTotal: _asInt(map['item_total']),
      hasMore: _asBool(map['has_more']),
      pageIndex: _asInt(map['page_index']),
    );
  }
}

/// `POST /offline/settings` 响应（保存/清除默认目录）
class OfflineSettingsResult {
  const OfflineSettingsResult({
    this.configured = false,
    this.diskId = 0,
    this.relPath = '',
  });

  final bool configured;
  final int diskId;
  final String relPath;

  factory OfflineSettingsResult.fromJson(Map<String, dynamic>? json) {
    final map = json ?? const <String, dynamic>{};
    return OfflineSettingsResult(
      configured: _asBool(map['configured']),
      diskId: _asInt(map['disk_id']),
      relPath: _asString(map['rel_path']),
    );
  }
}

/// `POST /offline/cancel|retry|delete-record` 通用动作响应
class OfflineTaskAction {
  const OfflineTaskAction({
    this.taskId = 0,
    this.status = '',
    this.deleted = false,
  });

  final int taskId;

  /// 动作后的任务状态（删除记录时为空串）
  final String status;

  /// 仅 `delete-record` 返回 true
  final bool deleted;

  factory OfflineTaskAction.fromJson(Map<String, dynamic>? json) {
    final map = json ?? const <String, dynamic>{};
    return OfflineTaskAction(
      taskId: _asInt(map['task_id']),
      status: _asString(map['status']),
      deleted: _asBool(map['deleted']),
    );
  }
}
