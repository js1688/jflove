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

  /// 媒体类型（`image` / `video` / 空串 = 老记录待识别；v1.8.3）
  final String mediaKind;

  const AlbumBackupRecord({
    required this.assetId,
    required this.diskId,
    required this.remoteRoot,
    required this.remotePath,
    required this.size,
    required this.backedAt,
    this.mediaKind = '',
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

  /// 最近上传（按 backed_at 倒序）
  Future<List<AlbumBackupRecord>> recentBackedUp({
    required int diskId,
    required String remoteRoot,
    int limit = 20,
  });

  /// 按媒体类型统计已备份数量（`image` / `video`；v1.8.3）
  Future<Map<String, int>> backedUpCountByKind({
    required int diskId,
    required String remoteRoot,
  });

  /// 老记录（media_kind 为空）的 asset id 集合，用于扫描时一次性回填
  Future<Set<String>> backedKindMissingIds({
    required int diskId,
    required String remoteRoot,
  });

  /// 回填某条记录的媒体类型
  Future<void> setMediaKind(String assetId, String mediaKind);

  /// 读取相册库内的键值元信息（上次扫描时间等）
  Future<String?> getMeta(String key);

  /// 写入相册库内的键值元信息
  Future<void> setMeta(String key, String value);

  /// 远端索引建立时间（null = 尚未建立）
  Future<DateTime?> remoteIndexBuiltAt({
    required int diskId,
    required String remoteRoot,
  });

  /// 全量替换远端索引（一次扫描建立完整索引后写入）
  Future<void> replaceRemoteIndex({
    required int diskId,
    required String remoteRoot,
    required Map<String, Map<String, int>> entries,
  });

  /// 上传成功后增量维护本地索引
  Future<void> upsertRemoteFile({
    required int diskId,
    required String remoteRoot,
    required String dir,
    required String name,
    required int size,
  });

  /// 读取本地持久化的远端索引（`dir -> {name: size}`）
  Future<Map<String, Map<String, int>>> loadRemoteIndex({
    required int diskId,
    required String remoteRoot,
  });

  /// 清空某目标的远端索引
  Future<void> clearRemoteIndex({
    required int diskId,
    required String remoteRoot,
  });

  /// 标记某资源已备份（同主键覆盖，只保留当前目标记录），并清除失败记录
  Future<void> markBackedUp(AlbumBackupRecord record);

  /// 记录一次失败（`attempts` 自增 1；v1.8.4：按目标归属）
  Future<void> markFailed(
    String assetId,
    String reason, {
    required int diskId,
    required String remoteRoot,
  });

  /// 清除某资源的失败记录
  Future<void> clearFailed(String assetId);

  /// 清空**指定目标**的失败记录（手动「重试失败项 / 立即检查」解锁自动重试）
  Future<void> clearAllFailures({
    required int diskId,
    required String remoteRoot,
  });

  /// 指定目标的失败记录列表（按最近尝试时间倒序，供「查看原因」）
  Future<List<AlbumBackupFailure>> failedEntries({
    required int diskId,
    required String remoteRoot,
  });

  /// 指定目标的失败数量
  Future<int> failedCount({
    required int diskId,
    required String remoteRoot,
  });

  /// 本地库 v4 → v5：把旧失败记录（无目标字段）回填到当前目标
  Future<void> backfillFailureTarget({
    required int diskId,
    required String remoteRoot,
  });

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
      version: 5,
      onCreate: (db, version) async {
        await db.execute('''
          CREATE TABLE album_backup (
            asset_id    TEXT PRIMARY KEY,
            disk_id     INTEGER NOT NULL,
            remote_root TEXT NOT NULL DEFAULT '',
            remote_path TEXT NOT NULL,
            size        INTEGER NOT NULL,
            backed_at   TEXT NOT NULL,
            media_kind  TEXT NOT NULL DEFAULT ''
          )
        ''');
        await db.execute('''
          CREATE TABLE album_backup_fail (
            asset_id    TEXT PRIMARY KEY,
            reason      TEXT NOT NULL,
            attempts    INTEGER NOT NULL DEFAULT 0,
            tried_at    TEXT NOT NULL,
            disk_id     INTEGER NOT NULL DEFAULT 0,
            remote_root TEXT NOT NULL DEFAULT ''
          )
        ''');
        await db.execute(
          'CREATE TABLE album_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)',
        );
        await db.execute(
          'CREATE TABLE album_remote_index ('
          'disk_id INTEGER NOT NULL, remote_root TEXT NOT NULL, '
          'dir TEXT NOT NULL, name TEXT NOT NULL, size INTEGER NOT NULL DEFAULT 0, '
          'PRIMARY KEY (disk_id, remote_root, dir, name))',
        );
        await db.execute(
          'CREATE TABLE album_remote_meta ('
          'disk_id INTEGER NOT NULL, remote_root TEXT NOT NULL, '
          'built_at TEXT NOT NULL, PRIMARY KEY (disk_id, remote_root))',
        );
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
        if (oldVersion < 4) {
          await db.execute(
            'CREATE TABLE IF NOT EXISTS album_remote_index ('
            'disk_id INTEGER NOT NULL, remote_root TEXT NOT NULL, '
            'dir TEXT NOT NULL, name TEXT NOT NULL, size INTEGER NOT NULL DEFAULT 0, '
            'PRIMARY KEY (disk_id, remote_root, dir, name))',
          );
          await db.execute(
            'CREATE TABLE IF NOT EXISTS album_remote_meta ('
            'disk_id INTEGER NOT NULL, remote_root TEXT NOT NULL, '
            'built_at TEXT NOT NULL, PRIMARY KEY (disk_id, remote_root))',
          );
        }
        if (oldVersion < 5) {
          // v1.8.4（C1）：失败记录按「磁盘 + 目标根」隔离，换目标不再串味
          await db.execute(
            'ALTER TABLE album_backup_fail '
            'ADD COLUMN disk_id INTEGER NOT NULL DEFAULT 0',
          );
          await db.execute(
            "ALTER TABLE album_backup_fail "
            "ADD COLUMN remote_root TEXT NOT NULL DEFAULT ''",
          );
        }
        if (oldVersion < 3) {
          await db.execute(
            "ALTER TABLE album_backup ADD COLUMN media_kind TEXT NOT NULL DEFAULT ''",
          );
          await db.execute(
            'CREATE TABLE IF NOT EXISTS album_meta '
            '(key TEXT PRIMARY KEY, value TEXT NOT NULL)',
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
  Future<List<AlbumBackupRecord>> recentBackedUp({
    required int diskId,
    required String remoteRoot,
    int limit = 20,
  }) async {
    final db = await _open();
    final rows = await db.query(
      'album_backup',
      where: 'disk_id = ? AND remote_root = ?',
      whereArgs: [diskId, remoteRoot],
      orderBy: 'backed_at DESC',
      limit: limit,
    );
    return rows
        .map(
          (row) => AlbumBackupRecord(
            assetId: row['asset_id'] as String,
            diskId: (row['disk_id'] as num).toInt(),
            remoteRoot: row['remote_root'] as String? ?? '',
            remotePath: row['remote_path'] as String,
            size: (row['size'] as num).toInt(),
            backedAt: row['backed_at'] as String,
            mediaKind: row['media_kind'] as String? ?? '',
          ),
        )
        .toList(growable: false);
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
      'media_kind': record.mediaKind,
    }, conflictAlgorithm: ConflictAlgorithm.replace);
    await db.delete(
      'album_backup_fail',
      where: 'asset_id = ?',
      whereArgs: [record.assetId],
    );
  }

  @override
  Future<void> markFailed(
    String assetId,
    String reason, {
    required int diskId,
    required String remoteRoot,
  }) async {
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
        'disk_id': diskId,
        'remote_root': remoteRoot,
      }, conflictAlgorithm: ConflictAlgorithm.replace);
    });
  }

  @override
  Future<void> backfillFailureTarget({
    required int diskId,
    required String remoteRoot,
  }) async {
    final db = await _open();
    // 旧记录（v4 及以前）没有目标归属：按当前目标认领，避免升级后失败数归零
    await db.update(
      'album_backup_fail',
      {'disk_id': diskId, 'remote_root': remoteRoot},
      where: "disk_id = 0 AND remote_root = ''",
    );
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
  Future<void> clearAllFailures({
    required int diskId,
    required String remoteRoot,
  }) async {
    final db = await _open();
    await db.delete(
      'album_backup_fail',
      where: 'disk_id = ? AND remote_root = ?',
      whereArgs: [diskId, remoteRoot],
    );
  }

  @override
  Future<List<AlbumBackupFailure>> failedEntries({
    required int diskId,
    required String remoteRoot,
  }) async {
    final db = await _open();
    final rows = await db.query(
      'album_backup_fail',
      where: 'disk_id = ? AND remote_root = ?',
      whereArgs: [diskId, remoteRoot],
      orderBy: 'tried_at DESC',
    );
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
  Future<int> failedCount({
    required int diskId,
    required String remoteRoot,
  }) async {
    final db = await _open();
    final rows = await db.rawQuery(
      'SELECT COUNT(*) AS c FROM album_backup_fail '
      'WHERE disk_id = ? AND remote_root = ?',
      [diskId, remoteRoot],
    );
    return (rows.first['c'] as num?)?.toInt() ?? 0;
  }

  @override
  Future<Map<String, int>> backedUpCountByKind({
    required int diskId,
    required String remoteRoot,
  }) async {
    final db = await _open();
    final rows = await db.rawQuery(
      'SELECT media_kind, COUNT(*) AS c FROM album_backup '
      'WHERE disk_id = ? AND remote_root = ? GROUP BY media_kind',
      [diskId, remoteRoot],
    );
    return {
      for (final row in rows)
        (row['media_kind'] as String? ?? ''): (row['c'] as num).toInt(),
    };
  }

  @override
  Future<Set<String>> backedKindMissingIds({
    required int diskId,
    required String remoteRoot,
  }) async {
    final db = await _open();
    final rows = await db.query(
      'album_backup',
      columns: ['asset_id'],
      where: "disk_id = ? AND remote_root = ? AND media_kind = ''",
      whereArgs: [diskId, remoteRoot],
    );
    return {for (final row in rows) row['asset_id'] as String};
  }

  @override
  Future<void> setMediaKind(String assetId, String mediaKind) async {
    final db = await _open();
    await db.update(
      'album_backup',
      {'media_kind': mediaKind},
      where: 'asset_id = ?',
      whereArgs: [assetId],
    );
  }

  @override
  Future<String?> getMeta(String key) async {
    final db = await _open();
    final rows = await db.query(
      'album_meta',
      columns: ['value'],
      where: 'key = ?',
      whereArgs: [key],
      limit: 1,
    );
    return rows.isEmpty ? null : rows.first['value'] as String;
  }

  @override
  Future<void> setMeta(String key, String value) async {
    final db = await _open();
    await db.insert(
      'album_meta',
      {'key': key, 'value': value},
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  @override
  Future<DateTime?> remoteIndexBuiltAt({
    required int diskId,
    required String remoteRoot,
  }) async {
    final db = await _open();
    final rows = await db.query(
      'album_remote_meta',
      columns: ['built_at'],
      where: 'disk_id = ? AND remote_root = ?',
      whereArgs: [diskId, remoteRoot],
      limit: 1,
    );
    if (rows.isEmpty) return null;
    return DateTime.tryParse(rows.first['built_at'] as String);
  }

  @override
  Future<void> replaceRemoteIndex({
    required int diskId,
    required String remoteRoot,
    required Map<String, Map<String, int>> entries,
  }) async {
    final db = await _open();
    await db.transaction((txn) async {
      await txn.delete(
        'album_remote_index',
        where: 'disk_id = ? AND remote_root = ?',
        whereArgs: [diskId, remoteRoot],
      );
      final batch = txn.batch();
      for (final dirEntry in entries.entries) {
        for (final fileEntry in dirEntry.value.entries) {
          batch.insert('album_remote_index', {
            'disk_id': diskId,
            'remote_root': remoteRoot,
            'dir': dirEntry.key,
            'name': fileEntry.key,
            'size': fileEntry.value,
          });
        }
      }
      await batch.commit(noResult: true);
      await txn.insert(
        'album_remote_meta',
        {
          'disk_id': diskId,
          'remote_root': remoteRoot,
          'built_at': DateTime.now().toIso8601String(),
        },
        conflictAlgorithm: ConflictAlgorithm.replace,
      );
    });
  }

  @override
  Future<void> upsertRemoteFile({
    required int diskId,
    required String remoteRoot,
    required String dir,
    required String name,
    required int size,
  }) async {
    final db = await _open();
    await db.insert(
      'album_remote_index',
      {
        'disk_id': diskId,
        'remote_root': remoteRoot,
        'dir': dir,
        'name': name,
        'size': size,
      },
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  @override
  Future<Map<String, Map<String, int>>> loadRemoteIndex({
    required int diskId,
    required String remoteRoot,
  }) async {
    final db = await _open();
    final rows = await db.query(
      'album_remote_index',
      columns: ['dir', 'name', 'size'],
      where: 'disk_id = ? AND remote_root = ?',
      whereArgs: [diskId, remoteRoot],
    );
    final result = <String, Map<String, int>>{};
    for (final row in rows) {
      final dir = row['dir'] as String;
      result.putIfAbsent(dir, () => {})[row['name'] as String] =
          (row['size'] as num).toInt();
    }
    return result;
  }

  @override
  Future<void> clearRemoteIndex({
    required int diskId,
    required String remoteRoot,
  }) async {
    final db = await _open();
    await db.delete(
      'album_remote_index',
      where: 'disk_id = ? AND remote_root = ?',
      whereArgs: [diskId, remoteRoot],
    );
    await db.delete(
      'album_remote_meta',
      where: 'disk_id = ? AND remote_root = ?',
      whereArgs: [diskId, remoteRoot],
    );
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
