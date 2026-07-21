import '../models/note.dart';
import '../utils/http_service.dart';

/// 笔记管理服务
/// 对标 jflove-desktop/src/services/note_service.py
///
/// 服务端接口对照：
///   GET    /api/v1/notes/list   - 笔记列表
///   GET    /api/v1/notes/read   - 读取笔记内容（body: token, filename）
///   POST   /api/v1/notes/write  - 新建或覆盖笔记（body: token, filename, content）
///   DELETE /api/v1/notes/       - 删除笔记（body: token, filename）
///   POST   /api/v1/notes/rename - 重命名（body: token, old_name, new_name）
///   GET    /api/v1/notes/disk-config   - 获取笔记目录配置
///   PUT    /api/v1/notes/disk-config   - 设置笔记目录配置
class NoteService {
  final HttpService _http;

  NoteService(this._http);

  /// 列出所有笔记
  Future<List<Note>> listNotes() async {
    final resp = await _http.encryptedGet('/api/v1/notes/list', {});
    final items = resp['notes'] as List<dynamic>;
    return items.map((e) => Note.fromJson(e as Map<String, dynamic>)).toList();
  }

  /// 读取笔记内容（服务端路径为 /read，不是 /get）
  Future<Note> getNote(String filename) async {
    final resp = await _http.encryptedGet('/api/v1/notes/read', {
      'filename': filename,
    });
    return Note.fromJson(resp);
  }

  /// 新建或覆盖笔记（服务端用 /write 同时处理新建和保存）
  Future<void> createNote(String filename) async {
    await _http.encryptedPost('/api/v1/notes/write', {
      'filename': filename,
      'content': '',
    });
  }

  /// 保存笔记内容（服务端用 POST /write，不是 PUT /save）
  Future<void> saveNote(String filename, String content) async {
    await _http.encryptedPost('/api/v1/notes/write', {
      'filename': filename,
      'content': content,
    });
  }

  /// 重命名笔记（服务端用 POST）
  Future<void> renameNote(String oldName, String newName) async {
    await _http.encryptedPost('/api/v1/notes/rename', {
      'old_name': oldName,
      'new_name': newName,
    });
  }

  /// 删除笔记
  Future<void> deleteNote(String name) async {
    await _http.encryptedDelete('/api/v1/notes', {'filename': name});
  }

  // ── 笔记目录配置 ─────────────────────────────────

  /// 获取当前用户的笔记磁盘配置及可用磁盘列表
  ///
  /// 对应服务端：GET /api/v1/notes/disk-config
  /// 返回：{ disk_id, path, disks }（disks 为可用磁盘列表）
  Future<Map<String, dynamic>> getNotesDiskConfig() async {
    return _http.encryptedGet('/api/v1/notes/disk-config', {});
  }

  /// 设置当前用户的笔记存储磁盘和子目录
  ///
  /// 对应服务端：PUT /api/v1/notes/disk-config
  /// [diskId] 目标磁盘 ID，null 表示清除配置
  /// [path] 磁盘内子目录相对路径，空字符串表示磁盘根目录
  Future<void> setNotesDiskConfig(int? diskId, {String path = ''}) async {
    await _http.encryptedPut('/api/v1/notes/disk-config', {
      'disk_id': diskId,
      'path': path,
    });
  }
}
