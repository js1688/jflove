/// 本地流式 HTTP 代理
///
/// 对标 jflove-desktop/src/components/stream_proxy.py。
///
/// 在 127.0.0.1 随机端口启动一个 HTTP 服务器，供 ExoPlayer（Android）
/// 通过 http://127.0.0.1:{port}/{token} 拉取媒体流。代理负责：
///   1. 解析 HTTP Range 请求头，把字节范围传给服务端 /api/v1/files/stream
///   2. 逐帧解密后写回 HttpResponse
///   3. 响应 206 Partial Content，含正确的 Content-Range / Content-Length
///
/// 安全保障：
///   - 只绑定 loopback 地址，外部无法访问
///   - URL 含一次性随机 token（UUID4），防止其他进程猜测
///
/// 用法：
///   final proxy = StreamProxy(
///     diskId: diskId, path: path, filename: name,
///     sessionKey: sessionKey, sessionId: sessionId,
///     serverUrl: serverUrl, token: jwtToken,
///   );
///   await proxy.start();
///   // 使用 proxy.url 作为 VideoPlayerController.network() 的源
///   // 关闭时：
///   proxy.close();
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

import 'package:dio/dio.dart';

import 'crypto.dart';
import 'stream_frame.dart';

/// 本地流式代理，供视频/音频播放器拉取加密媒体文件
class StreamProxy {
  final int diskId;
  final String path;
  final String filename;
  final Uint8List sessionKey;
  final String sessionId;
  final String serverUrl;
  final String jwtToken;

  late final String _token;
  HttpServer? _server;
  bool _closed = false;
  int _port = 0;

  // 元数据缓存（首次请求后填充）
  int _fileSize = 0;
  String _contentType = 'application/octet-stream';
  // v1.4.0：time 修复流模式与媒体时长（秒），供 GET 线性时间 seek 映射
  String _streamMode = 'byte';
  double _duration = 0.0;
  bool _metaFetched = false;

  StreamProxy({
    required this.diskId,
    required this.path,
    required this.filename,
    required this.sessionKey,
    required this.sessionId,
    required this.serverUrl,
    required this.jwtToken,
  }) {
    _token = _generateToken();
  }

  /// 本地代理 URL（含一次性 token）
  String get url => 'http://127.0.0.1:$_port/$_token';

  /// 启动代理服务器
  Future<void> start() async {
    _server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    _port = _server!.port;

    _server!.listen(
      (HttpRequest request) {
        _handleRequest(request);
      },
      onError: (error) {
        // 客户端断开等，静默忽略
      },
    );
  }

  /// 停止代理，关闭服务器
  void close() {
    _closed = true;
    _server?.close();
    _server = null;
  }

  // ── 请求处理 ──────────────────────────────────────

  void _handleRequest(HttpRequest request) {
    if (_closed) {
      _sendError(request.response, 503, 'proxy closed');
      return;
    }

    // 验证 URL token
    if (!request.uri.path.endsWith('/$_token')) {
      _sendError(request.response, 404, 'invalid token');
      return;
    }

    switch (request.method) {
      case 'HEAD':
        _handleHead(request);
        break;
      case 'GET':
        _handleGet(request);
        break;
      default:
        _sendError(request.response, 405, 'method not allowed');
    }
  }

  // ── HEAD 请求 ─────────────────────────────────────

  Future<void> _handleHead(HttpRequest request) async {
    try {
      await _ensureMeta();
      final resp = request.response;
      resp.statusCode = 200;
      resp.headers.set('Content-Type', _contentType);
      resp.headers.set('Content-Length', _fileSize.toString());
      // v1.4.0：time 修复流为实时生成、总字节不可预知，不支持字节 seek，
      // 不声明 Accept-Ranges，让播放器按顺序流式播放
      if (_streamMode != 'time') {
        resp.headers.set('Accept-Ranges', 'bytes');
      }
      resp.headers.set('Connection', 'close');
      await resp.close();
    } catch (e) {
      _sendError(request.response, 500, e.toString());
    }
  }

  // ── GET 请求（含 Range seek）──────────────────────

  Future<void> _handleGet(HttpRequest request) async {
    try {
      await _ensureMeta();
    } catch (e) {
      _sendError(request.response, 500, e.toString());
      return;
    }

    final (rangeStart, rangeEnd) = _parseRange(
      request.headers.value('Range') ?? '',
      _fileSize,
    );

    try {
      final resp = request.response;
      // ── v1.4.0：time 修复流分支 ──
      // 修复流总字节不可预知 → 200 + chunked（无 Content-Length）；
      // 播放器字节 Range 用平均码率线性近似映射为时间起点，服务端 -ss 重拉。
      if (_streamMode == 'time') {
        final seconds = mapRangeToSeconds(rangeStart, _fileSize, _duration);
        final stream = await _fetchStreamRange(
          0,
          0,
          rangeStartSeconds: seconds,
        );
        resp.statusCode = 200;
        resp.headers.set('Content-Type', _contentType);
        resp.headers.set('Connection', 'close');
        await for (final chunk in stream) {
          if (_closed) break;
          resp.add(chunk);
        }
        await resp.close();
        return;
      }

      final stream = await _fetchStreamRange(rangeStart, rangeEnd);
      final contentLength = rangeEnd - rangeStart;
      resp.statusCode = 206;
      resp.headers.set('Content-Type', _contentType);
      resp.headers.set('Content-Length', contentLength.toString());
      resp.headers.set(
        'Content-Range',
        'bytes $rangeStart-${rangeEnd - 1}/$_fileSize',
      );
      resp.headers.set('Accept-Ranges', 'bytes');
      resp.headers.set('Connection', 'close');

      await for (final chunk in stream) {
        if (_closed) break;
        resp.add(chunk);
      }
      await resp.close();
    } catch (e) {
      // 客户端断开（seek/关闭）属于正常流程
      if (e is HttpException || e is SocketException) {
        return;
      }
      _sendError(request.response, 500, e.toString());
    }
  }

  // ── 元数据缓存 ────────────────────────────────────

  Future<void> _ensureMeta() async {
    if (_metaFetched) return;

    // 发 range(0,0) 请求拿元数据帧；v1.4.0：探测请求也声明支持时间 range，
    // 服务端对损坏文件返回 time meta（file_size / duration）
    final stream = await _fetchStreamRange(0, 0, rangeStartSeconds: 0);
    // 只消费到首个数据帧：meta 帧解析在数据帧前完成（time 模式下服务端会
    // 继续输出完整修复流，首帧到达后立即停止避免浪费转码）
    await for (final _ in stream) {
      break;
    }
  }

  // ── 范围解析 ──────────────────────────────────────

  (int, int) _parseRange(String raw, int fileSize) {
    if (raw.startsWith('bytes=')) {
      final parts = raw.substring(6).split('-');
      try {
        if (parts[0].isEmpty) {
          // Suffix range: bytes=-N
          final suffix = int.parse(parts[1]);
          return (max(0, fileSize - suffix), fileSize);
        }
        final start = int.parse(parts[0]);
        final end = parts.length > 1 && parts[1].isNotEmpty
            ? int.parse(parts[1]) + 1
            : fileSize;
        return (start, min(end, fileSize));
      } catch (_) {}
    }
    return (0, fileSize);
  }

  // ── 服务端流式请求 ────────────────────────────────

  /// 向服务端发起流式范围请求，返回解密后的明文字节流
  ///
  /// 每次请求的第一帧均为元数据帧（JSON: file_size / content_type 等），
  /// 自动过滤不传给调用方；后续帧为实际文件数据。
  ///
  /// [rangeStartSeconds]：v1.4.0 修复流专用时间起点（秒）。非 null 即向服务端
  /// 声明支持时间 range 修复流（time 模式）；null 表示旧字节 range 语义。
  Future<Stream<Uint8List>> _fetchStreamRange(
    int rangeStart,
    int rangeEnd, {
    double? rangeStartSeconds,
  }) async {
    // 构建请求体
    final payload = {
      'token': jwtToken,
      'disk_id': diskId,
      'path': path,
      'filename': filename,
      'range_start': rangeStart,
      'range_end': rangeEnd,
    };
    if (rangeStartSeconds != null) {
      payload['range_start_seconds'] = rangeStartSeconds;
    }
    final plainBytes = utf8.encode(jsonEncode(payload));
    final envelope = CryptoUtils.encryptEnvelope(sessionKey, plainBytes);

    final dio = Dio(
      BaseOptions(
        connectTimeout: const Duration(seconds: 5),
        receiveTimeout: const Duration(seconds: 30),
      ),
    );

    final resp = await dio.get(
      '$serverUrl/api/v1/files/stream',
      data: {'nonce': envelope.nonce, 'ciphertext': envelope.ciphertext},
      options: Options(
        headers: {'X-Session-ID': sessionId},
        responseType: ResponseType.stream,
        validateStatus: (_) => true,
      ),
    );

    if (resp.statusCode != null && resp.statusCode! >= 400) {
      throw Exception('服务端返回错误: ${resp.statusCode}');
    }

    final rawStream = resp.data.stream as Stream<List<int>>;

    // 通过 StreamFrameParser 逐帧解密
    final parser = StreamFrameParser(
      sessionKey: sessionKey,
      rawStream: rawStream,
    );

    // 每个请求流的第一帧始终是元数据帧，必须无条件跳过
    bool isFirstChunk = true;

    return parser.parse().transform(
      StreamTransformer<Uint8List, Uint8List>.fromHandlers(
        handleData: (chunk, sink) {
          if (isFirstChunk) {
            isFirstChunk = false;
            // 第一帧始终检查是否为元数据 JSON
            if (chunk.length < 2048) {
              try {
                final json =
                    jsonDecode(utf8.decode(chunk)) as Map<String, dynamic>;
                if (json.containsKey('file_size')) {
                  _fileSize = json['file_size'] as int;
                  _contentType =
                      (json['content_type'] as String?) ??
                      'application/octet-stream';
                  // v1.4.0：time 修复流模式与时长（GET 线性时间 seek 映射）
                  _streamMode = (json['stream_mode'] as String?) ?? 'byte';
                  _duration = ((json['duration'] as num?) ?? 0).toDouble();
                  _metaFetched = true;
                  return; // 元数据帧不传给播放器
                }
                if (json['type'] == 'error') {
                  sink.addError(Exception(json['message'] ?? '服务端返回错误'));
                  return;
                }
              } catch (_) {
                // 非 JSON，正常数据帧（理论上不应出现，但做容错）
              }
            }
          }
          sink.add(chunk);
        },
      ),
    );
  }

  // ── 辅助 ──────────────────────────────────────────

  /// 把播放器字节偏移线性映射为时间（秒，平均码率近似）。
  ///
  /// 供 time 修复流 seek 使用：seconds = offset / file_size * duration；
  /// 任一参数非正（含零保护）时返回 0（从头开始）。
  static double mapRangeToSeconds(
    int rangeStart,
    int fileSize,
    double duration,
  ) {
    if (rangeStart <= 0 || fileSize <= 0 || duration <= 0) return 0.0;
    return rangeStart / fileSize * duration;
  }

  void _sendError(HttpResponse resp, int code, String msg) {
    resp.statusCode = code;
    resp.headers.set('Connection', 'close');
    resp.write(msg);
    resp.close();
  }

  static String _generateToken() {
    final rng = Random.secure();
    final bytes = List<int>.generate(16, (_) => rng.nextInt(256));
    return bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  }
}
