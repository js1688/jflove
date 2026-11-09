import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

/// 传输断点续传本地记录（v1.9.0）
///
/// 服务端是「已收分片」的唯一真相；这里的记录只作兜底：
///  - 上传：记录最近一次 upload_id / 已发分片 / 过期时间；
///  - 下载：分片文件旁的 `.jflove-part.json` 记录远端版本/大小（由传输层直接读写）。
class TransferResumeStore {
  final String? _overridePath;

  // ignore: prefer_initializing_formals - 私有字段名不改，避免调用方暴露私有参数名
  TransferResumeStore({String? overridePath}) : _overridePath = overridePath;

  Future<String> _path() async {
    if (_overridePath != null) return _overridePath;
    final dir = await getApplicationSupportDirectory();
    return '${dir.path}${Platform.pathSeparator}transfer_resume.json';
  }

  /// 上传续传键：磁盘 + 目录 + 文件名 + 大小 + 内容哈希
  static String uploadKey({
    required int diskId,
    required String remoteDir,
    required String filename,
    required int size,
    required String fileHash,
  }) => '$diskId|$remoteDir|$filename|$size|$fileHash';

  Future<Map<String, dynamic>> _loadAll() async {
    try {
      final file = File(await _path());
      if (!await file.exists()) return <String, dynamic>{};
      final raw = jsonDecode(await file.readAsString());
      return raw is Map<String, dynamic> ? raw : <String, dynamic>{};
    } catch (_) {
      return <String, dynamic>{};
    }
  }

  Future<void> _writeAll(Map<String, dynamic> data) async {
    try {
      final file = File(await _path());
      await file.parent.create(recursive: true);
      await file.writeAsString(jsonEncode(data));
    } catch (_) {
      // 本地记录是兜底能力：平台通道不可用（测试/特殊环境）时静默跳过，
      // 不能阻断上传本身（服务端仍以磁盘分片为准）。
    }
  }

  /// 读取上传续传记录（不存在/损坏返回 null）
  Future<Map<String, dynamic>?> loadUpload(String key) async {
    final all = await _loadAll();
    final item = all['upload:$key'];
    return item is Map<String, dynamic> ? item : null;
  }

  /// 保存上传续传记录
  Future<void> saveUpload(
    String key, {
    required String uploadId,
    required Set<int> sentChunks,
    required int fileSize,
    required double expiresAt,
  }) async {
    final all = await _loadAll();
    all['upload:$key'] = {
      'upload_id': uploadId,
      'sent_chunks': sentChunks.toList()..sort(),
      'file_size': fileSize,
      'expires_at': expiresAt,
      'updated_at': DateTime.now().toIso8601String(),
    };
    await _writeAll(all);
  }

  /// 清除上传续传记录
  Future<void> clearUpload(String key) async {
    final all = await _loadAll();
    all.remove('upload:$key');
    await _writeAll(all);
  }
}
