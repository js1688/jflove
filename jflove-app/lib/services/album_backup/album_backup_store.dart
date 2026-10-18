import 'package:path/path.dart' as p;
import 'package:sqflite/sqflite.dart';

/// 已备份资源记录（v1.7.0）
class AlbumBackupRecord {
  final String assetId;
  final int diskId;
  final String remotePath;
  final int size;
  final String backedAt;

  const AlbumBackupRecord({
    required this.assetId,
    required this.diskId,
    required this.remotePath,
    required this.size,
    required this.backedAt,
  });
}

/// 相册备份本地记录（sqflite）
///
/// 为什么用 SQLite 而不是 JSON：相册可能有上万条记录，
/// 每次全量读写 JSON 会随数量线性变慢；这里只做「按主键查/写」。
class AlbumBackupStore {
  Database? _db;

  Future<Database> _open() async {
    final cached = _db;
    if (cached != null) return cached;
    final dir = await getDatabasesPath();
    final db = await openDatabase(
      p.join(dir, 'album_backup.db'),
      version: 1,
      onCreate: (db, version) async {
        await db.execute('''
          CREATE TABLE album_backup (
            asset_id    TEXT PRIMARY KEY,
            disk_id     INTEGER NOT NULL,
            remote_path TEXT NOT NULL,
            size        INTEGER NOT NULL,
            backed_at   TEXT NOT NULL
          )
        ''');
        await db.execute('''
          CREATE TABLE album_backup_fail (
            asset_id TEXT PRIMARY KEY,
            reason   TEXT NOT NULL,
            tried_at TEXT NOT NULL
          )
        ''');
      },
    );
    _db = db;
    return db;
  }

  /// 全部已备份的 asset id（用于扫描时快速跳过）
  Future<Set<String>> backedUpIds() async {
    final db = await _open();
    final rows = await db.query('album_backup', columns: ['asset_id']);
    return rows.map((r) => r['asset_id'] as String).toSet();
  }

  /// 标记某资源已备份
  Future<void> markBackedUp(AlbumBackupRecord record) async {
    final db = await _open();
    await db.insert(
      'album_backup',
      {
        'asset_id': record.assetId,
        'disk_id': record.diskId,
        'remote_path': record.remotePath,
        'size': record.size,
        'backed_at': record.backedAt,
      },
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
    await db.delete(
      'album_backup_fail',
      where: 'asset_id = ?',
      whereArgs: [record.assetId],
    );
  }

  /// 记录一次失败（可在传输页重试）
  Future<void> markFailed(String assetId, String reason) async {
    final db = await _open();
    await db.insert(
      'album_backup_fail',
      {
        'asset_id': assetId,
        'reason': reason,
        'tried_at': DateTime.now().toIso8601String(),
      },
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  /// 清除某资源的失败记录
  Future<void> clearFailed(String assetId) async {
    final db = await _open();
    await db.delete(
      'album_backup_fail',
      where: 'asset_id = ?',
      whereArgs: [assetId],
    );
  }

  /// 已备份数量
  Future<int> backedUpCount() async {
    final db = await _open();
    final rows = await db.rawQuery('SELECT COUNT(*) AS c FROM album_backup');
    return (rows.first['c'] as num?)?.toInt() ?? 0;
  }

  /// 失败数量
  Future<int> failedCount() async {
    final db = await _open();
    final rows = await db.rawQuery('SELECT COUNT(*) AS c FROM album_backup_fail');
    return (rows.first['c'] as num?)?.toInt() ?? 0;
  }

  /// 失败资源 ID 列表
  Future<List<String>> failedIds() async {
    final db = await _open();
    final rows = await db.query('album_backup_fail', columns: ['asset_id']);
    return rows.map((r) => r['asset_id'] as String).toList();
  }

  /// 清空全部记录（用户在设置页手动清理时使用）
  Future<void> clearAll() async {
    final db = await _open();
    await db.delete('album_backup');
    await db.delete('album_backup_fail');
  }

  /// 关闭数据库连接
  Future<void> close() async {
    final db = _db;
    _db = null;
    await db?.close();
  }
}
