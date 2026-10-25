import 'dart:io';

import 'package:photo_manager/photo_manager.dart';

/// 相册资源媒体类型（与 photo_manager 解耦，便于单测注入假数据）
enum AlbumMediaKind {
  /// 图片
  image,

  /// 视频
  video,

  /// 其它（音频/未知，不参与相册备份）
  other,
}

/// 相册计数范围（对应 photo_manager 的 RequestType）
enum AlbumCountScope {
  /// 全部图片 + 视频
  all,

  /// 仅图片
  image,

  /// 仅视频
  video,
}

/// 单个相册资源（抽象层，屏蔽 photo_manager 的平台通道）
///
/// 为什么要有这一层：`AssetEntity.file` 依赖 Android/iOS 平台通道，
/// 纯 Dart 单测里无法构造，也无法注入"坏资源"来验证单资源隔离。
class AlbumAsset {
  /// 平台侧资源 ID（MediaStore ID / PHAsset localIdentifier）
  final String id;

  /// 原始文件名（相册里显示的名字，仅用于远端同名比对，**不写日志**）
  final String? title;

  /// 媒体类型
  final AlbumMediaKind kind;

  /// 创建时间（决定远端 `YYYY/MM` 目录）
  final DateTime createDateTime;

  /// 打开本地文件（平台能力，可能返回 null 或抛异常）
  final Future<File?> Function() openFile;

  AlbumAsset({
    required this.id,
    required this.title,
    required this.kind,
    required this.createDateTime,
    required this.openFile,
  });

  /// 远端文件名：与上传时保持一致（无标题时回退 `asset_<id>`）
  String get filename {
    final name = title?.trim();
    if (name == null || name.isEmpty) return 'asset_$id';
    return name;
  }

  /// 本地文件（失败/仅云端时返回 null 或抛异常）
  Future<File?> get file => openFile();
}

/// 相册资源数据源接口
///
/// 生产实现 = [PhotoManagerAlbumAssetSource]；单测注入假实现即可
/// 验证坏资源隔离、视频开关计数、跨轮游标等逻辑，不需要真机。
abstract class AlbumAssetSource {
  /// 请求相册读取权限
  Future<bool> ensurePermission();

  /// 读取指定范围的相册总数
  Future<int> albumCount(AlbumCountScope scope);

  /// 按 `[start, end)` 分页读取资源（端点上界按相册尾部自动截断）
  Future<List<AlbumAsset>> listAssets({required int start, required int end});

  /// 按 ID 找回资源（失败项自动重试用）；找不到返回 null
  Future<AlbumAsset?> findAsset(String id);
}

/// 基于 `photo_manager` 的生产实现
class PhotoManagerAlbumAssetSource implements AlbumAssetSource {
  AssetPathEntity? _allPath;

  @override
  Future<bool> ensurePermission() async {
    final state = await PhotoManager.requestPermissionExtend();
    return state.hasAccess;
  }

  @override
  Future<int> albumCount(AlbumCountScope scope) async {
    try {
      final type = switch (scope) {
        AlbumCountScope.all => RequestType.common,
        AlbumCountScope.image => RequestType.image,
        AlbumCountScope.video => RequestType.video,
      };
      final paths = await PhotoManager.getAssetPathList(
        type: type,
        onlyAll: true,
      );
      if (paths.isEmpty) return 0;
      return await paths.first.assetCountAsync;
    } catch (_) {
      // 无权限 / 平台异常：返回 0，不中断备份主流程
      return 0;
    }
  }

  Future<AssetPathEntity?> _allPathEntity() async {
    final cached = _allPath;
    if (cached != null) return cached;
    final paths = await PhotoManager.getAssetPathList(
      type: RequestType.common,
      onlyAll: true,
    );
    if (paths.isEmpty) return null;
    _allPath = paths.first;
    return _allPath;
  }

  @override
  Future<List<AlbumAsset>> listAssets({
    required int start,
    required int end,
  }) async {
    final path = await _allPathEntity();
    if (path == null) return const [];
    final entities = await path.getAssetListRange(start: start, end: end);
    return entities.map(_wrap).toList(growable: false);
  }

  @override
  Future<AlbumAsset?> findAsset(String id) async {
    try {
      final entity = await AssetEntity.fromId(id);
      return entity == null ? null : _wrap(entity);
    } catch (_) {
      return null;
    }
  }

  AlbumAsset _wrap(AssetEntity entity) => AlbumAsset(
    id: entity.id,
    title: entity.title,
    kind: _kindOf(entity.type),
    createDateTime: entity.createDateTime,
    openFile: () => entity.file,
  );

  AlbumMediaKind _kindOf(AssetType type) {
    if (type == AssetType.image) return AlbumMediaKind.image;
    if (type == AssetType.video) return AlbumMediaKind.video;
    return AlbumMediaKind.other;
  }
}
