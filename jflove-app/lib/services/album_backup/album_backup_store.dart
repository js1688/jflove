import 'package:path/path.dart' as p;
import 'package:sqflite/sqflite.dart';

/// 失败原因的固定中文枚举（用于界面展示与「永久/暂时」判定）
///
/// 约束：**不含路径 / 文件名**（`AGENTS.md §9.4`）。
class AlbumBackupFailureReason {
  /// 平台记录存在但本地取不到文件（通常只有云端副本；重试一般无效）
  static const String unavailable = '文件不可用（可能仅在云端）';

  /// 本地文件读取失败（权限 / IO 抖动；可重试）
  static const String readFailed = '读取失败';

  /// 提交上传或上传过程失败（网络 / 服务端；可重试）
  static const String uploadFailed = '上传失败';

  /// 上传被用户取消
  static const String cancelled = '上传已取消';

  /// 永久不可用的原因集合：达到重试上限后不再自动重试
  static const Set<String> permanentReasons = {unavailable};

  /// 该原因是否属于「永久不可用」
  static bool isPermanent(String reason) => permanentReasons.contains(reason);
}

/// 已备份资源记录（v1.8.2：新增 `remoteRoot`，把记录限定到「磁盘 + 目标目录」）
class AlbumBackupRecord {
  /// 相册资源 ID
  final String assetId;

  /// 远端磁盘 ID
  final int diskId;

  /// 远端目标根目录（相对磁盘根，空串 = 根）
  final String remoteRoot;

  /// 远端完整相对路径（`<root>/YYYY/MM/文件名`）
  final String remotePath;

  /// 文件大小（字节）
  final int size;

  /// 记录时间（ISO8601）
  final String backedAt;

  const AlbumBackupRecord({
    required this.assetId,
    required this.diskId,
    required this.remoteRoot,
    required this.remotePath,
    required this.size,
    required this.backedAt,
  });
}

/// 一条失败记录（v1.8.2：带重试次数与中文原因）
class AlbumBackupFailure {
  /// 相册资源 ID
  final String assetId;

  /// 中文失败原因（不含路径）
  final String reason;

  /// 累计尝试次数（自动重试上限 3 次）
  final int attempts;

  /// 最近一次尝试时间（ISO8601）
  final String triedAt;

  const AlbumBackupFailure({
    required this.assetId,
    required this.reason,
    required this.attempts,
    required this.triedAt,
  });

  /// 是否属于永久不可用（界面可据此区分「暂时失败」）
  bool get permanent => AlbumBackupFailureReason.isPermanent(reason);
}

/// 相册备份本地存储接口
///
/// 生产实现 = [SqfliteAlbumBackupStore]；单测注入内存假实现，避免依赖
/// sqflite 平台通道。
abstract class AlbumBackupStore {
  /// 本地库 v1 → v2：把旧记录（`remote_root` 为空）按当前目标回填
  Future<void> backfillRemoteRoot(int diskId, String remoteRoot);

  /// 指定「磁盘 + 目标目录」下已备份的 asset id 集合
  Future<Set<String>> backedUpIds({
    required int diskId,
    required String remoteRoot,
  });

  /// 指定「磁盘 + 目标目录」下已备份数量
  Future<int> backedUpCount({required int diskId, required String remoteRoot});

  /// 标记某资源已备份（同主键覆盖，只保留当前目标记录），并清除失败记录
  Future<void> markBackedUp(AlbumBackupRecord record);

  /// 记录一次失败（`attempts` 自增 1）
  Future<void> markFailed(String assetId, String reason);

  /// 清除某资源的失败记录
  Future<void> clearFailed(String assetId);

  /// 清空全部失败记录（手动「重试失败项 / 立即检查」解锁自动重试）
  Future<void> clearAllFailures();

  /// 失败记录列表（按最近尝试时间倒序，供「查看原因」）
  Future<List<AlbumBackupFailure>> failedEntries();

  /// 失败数量
  Future<int> failedCount();

  /// 清空全部备份与失败记录（不影响远端文件）
  Future<void> clearAll();

  /// 关闭数据库连接
  Future<void> close();
}

/// sqflite 实现（本地库 v2）
///
/// 为什么用 SQLite 而不是 JSON：相册可能有上万条记录，
/// 每次全量读写 JSON 会随数量线性变慢；这里只做「按主键查/写」。
class SqfliteAlbumBackupStore implements AlbumBackupStore {
  Database? _db;

  Future<Database> _open() async {
    final cached = _db;
    if (cached != null) return cached;
    final dir = await getDatabasesPath();
    final db = await openDatabase(
      p.join(dir, 'album_backup.db'),
      version: 2,
      onCreate: (db, version) async {
        await db.execute('''
          CREATE TABLE album_backup (
            asset_id    TEXT PRIMARY KEY,
            disk_id     INTEGER NOT NULL,
            remote_root TEXT NOT NULL DEFAULT '',
            remote_path TEXT NOT NULL,
            size        INTEGER NOT NULL,
            backed_at   TEXT NOT NULL
          )
        ''');
        await db.execute('''
          CREATE TABLE album_backup_fail (
            asset_id TEXT PRIMARY KEY,
            reason   TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            tried_at TEXT NOT NULL
          )
        ''');
      },
      onUpgrade: (db, oldVersion, newVersion) async {
        // v1 → v2：记录归属（remote_root）+ 失败重试次数（attempts）
        if (oldVersion < 2) {
          await db.execute(
            "ALTER TABLE album_backup ADD COLUMN remote_root TEXT NOT NULL DEFAULT ''",
          );
          await db.execute(
            'ALTER TABLE album_backup_fail ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0',
          );
        }
      },
    );
    _db = db;
    return db;
  }

  @override
  Future<void> backfillRemoteRoot(int diskId, String remoteRoot) async {
    final db = await _open();
    await db.update(
      'album_backup',
      {'remote_root': remoteRoot},
      where: "disk_id = ? AND remote_root = ''",
      whereArgs: [diskId],
    );
  }

  @override
  Future<Set<String>> backedUpIds({
    required int diskId,
    required String remoteRoot,
  }) async {
    final db = await _open();
    final rows = await db.query(
      'album_backup',
      columns: ['asset_id'],
      where: 'disk_id = ? AND remote_root = ?',
      whereArgs: [diskId, remoteRoot],
    );
    return rows.map((r) => r['asset_id'] as String).toSet();
  }

  @override
  Future<int> backedUpCount({
    required int diskId,
    required String remoteRoot,
  }) async {
    final db = await _open();
    final rows = await db.rawQuery(
      'SELECT COUNT(*) AS c FROM album_backup '
      'WHERE disk_id = ? AND remote_root = ?',
      [diskId, remoteRoot],
    );
    return (rows.first['c'] as num?)?.toInt() ?? 0;
  }

  @override
  Future<void> markBackedUp(AlbumBackupRecord record) async {
    final db = await _open();
    await db.insert('album_backup', {
      'asset_id': record.assetId,
      'disk_id': record.diskId,
      'remote_root': record.remoteRoot,
      'remote_path': record.remotePath,
      'size': record.size,
      'backed_at': record.backedAt,
    }, conflictAlgorithm: ConflictAlgorithm.replace);
    await db.delete(
      'album_backup_fail',
      where: 'asset_id = ?',
      whereArgs: [record.assetId],
    );
  }

  @override
  Future<void> markFailed(String assetId, String reason) async {
    final db = await _open();
    await db.transaction((txn) async {
      final rows = await txn.query(
        'album_backup_fail',
        columns: ['attempts'],
        where: 'asset_id = ?',
        whereArgs: [assetId],
      );
      final previous = rows.isEmpty
          ? 0
          : ((rows.first['attempts'] as num?)?.toInt() ?? 0);
      await txn.insert('album_backup_fail', {
        'asset_id': assetId,
        'reason': reason,
        'attempts': previous + 1,
        'tried_at': DateTime.now().toIso8601String(),
      }, conflictAlgorithm: ConflictAlgorithm.replace);
    });
  }

  @override
  Future<void> clearFailed(String assetId) async {
    final db = await _open();
    await db.delete(
      'album_backup_fail',
      where: 'asset_id = ?',
      whereArgs: [assetId],
    );
  }

  @override
  Future<void> clearAllFailures() async {
    final db = await _open();
    await db.delete('album_backup_fail');
  }

  @override
  Future<List<AlbumBackupFailure>> failedEntries() async {
    final db = await _open();
    final rows = await db.query('album_backup_fail', orderBy: 'tried_at DESC');
    return rows
        .map(
          (r) => AlbumBackupFailure(
            assetId: r['asset_id'] as String,
            reason: r['reason'] as String? ?? '',
            attempts: (r['attempts'] as num?)?.toInt() ?? 0,
            triedAt: r['tried_at'] as String? ?? '',
          ),
        )
        .toList(growable: false);
  }

  @override
  Future<int> failedCount() async {
    final db = await _open();
    final rows = await db.rawQuery(
      'SELECT COUNT(*) AS c FROM album_backup_fail',
    );
    return (rows.first['c'] as num?)?.toInt() ?? 0;
  }

  @override
  Future<void> clearAll() async {
    final db = await _open();
    await db.delete('album_backup');
    await db.delete('album_backup_fail');
  }

  @override
  Future<void> close() async {
    final db = _db;
    _db = null;
    await db?.close();
  }
}
