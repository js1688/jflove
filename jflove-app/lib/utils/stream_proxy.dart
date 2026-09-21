/// 本地流式 HTTP 代理（v1.4.2 纯 byte 模式）
///
/// 对标 jflove-desktop/src/components/stream_proxy.py。
///
/// 在 127.0.0.1 随机端口启动一个 HTTP 服务器，供 ExoPlayer（Android）
/// 通过 http://127.0.0.1:{port}/{token} 拉取媒体流。代理负责：
///   1. 解析 HTTP Range 请求头，把字节范围传给服务端 /api/v1/files/stream
///   2. 逐帧解密后写回 HttpResponse
///   3. 响应 206 Partial Content，含正确的 Content-Range / Content-Length
///
/// v1.4.2 变更：移除 time 修复流分支（seek()/_seekSeconds/_seekVersion/
/// duration）——服务端已不再实时修复，本代理只处理健康文件字节流；
/// 新增 repairTaskId 支持（修复中心「验证播放」经同一代理拉取修复产物）。
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
  // v1.4.2：修复产物验证播放（>0 时 stream 请求携带 repair_task_id，
  // 服务端流式返回修复任务产物；diskId/path/filename 不再指向原文件）
  final int repairTaskId;

  late final String _token;
  HttpServer? _server;
  bool _closed = false;
  int _port = 0;

  // 元数据缓存（首次请求后填充）
  int _fileSize = 0;
  String _contentType = 'application/octet-stream';
  bool _metaFetched = false;
  // v1.4.2：最近一次拉流错误（供预览页识别「需修复」错误码弹修复引导）
  String lastError = '';

  StreamProxy({
    required this.diskId,
    required this.path,
    required this.filename,
    required this.sessionKey,
    required this.sessionId,
    required this.serverUrl,
    required this.jwtToken,
    this.repairTaskId = 0,
  }) {
    _token = _generateToken();
  }

  /// 本地代理 URL（含一次性 token；v1.4.2 不再带 seek 版本号 query）
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
      // v1.4.2：恒为 byte 模式，支持字节 range（原生 seek）
      resp.headers.set('Accept-Ranges', 'bytes');
      resp.headers.set('Connection', 'close');
      await resp.close();
    } catch (e) {
      lastError = e.toString();
      _sendError(request.response, 500, e.toString());
    }
  }

  // ── GET 请求（含 Range seek）──────────────────────

  Future<void> _handleGet(HttpRequest request) async {
    try {
      await _ensureMeta();
    } catch (e) {
      lastError = e.toString();
      _sendError(request.response, 500, e.toString());
      return;
    }

    final (rangeStart, rangeEnd) = _parseRange(
      request.headers.value('Range') ?? '',
      _fileSize,
    );

    try {
      final resp = request.response;
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

      // ⚠ 必须**逐块 flush**（v1.5.0 反馈修复：局域网播大视频卡死）。
      //
      // `resp.add()` 是**非阻塞**的：它只把数据塞进 `HttpResponse` 的**内存缓冲**。
      // 不 flush 时这条循环会以上游（服务端）能送多快就多快地跑：
      //   · 上游越**快**（局域网/本机），内存缓冲涨得越猛 —— 播放器只按播放速率取数据，
      //     取不动的部分全堆在内存里。1GB 的视频足以把内存吃满、GC 疯狂抖动 → 界面卡死；
      //   · 队列里 64KB 一帧的解密（纯 Dart 的 ChaCha20-Poly1305）也跑在**主 isolate** 上，
      //     上游不停就永远不让出事件循环 → 界面直接失去响应。
      // 这正好解释"**移动网络（慢）能正常播、局域网（快）反而卡死**"这个反直觉现象：
      // 慢链路自带节流，快链路把背压问题暴露出来。
      //
      // `flush()` 会等到数据真正被 socket 接收，于是背压一路传导回上游：
      // **播放器不取，就不继续下载、不继续解密**，内存占用与上游速率彻底解耦。
      await for (final chunk in stream) {
        if (_closed) break;
        resp.add(chunk);
        await resp.flush();
      }
      await resp.close();
    } catch (e) {
      lastError = e.toString();
      // 客户端断开（seek/关闭）属于正常流程；此时也必须把响应关掉，
      // 否则连接会一直挂着（旧实现直接 return，响应既不 close 也不清理）。
      if (e is HttpException || e is SocketException || _closed) {
        try {
          await request.response.close();
        } catch (_) {}
        return;
      }
      _sendError(request.response, 500, e.toString());
    }
  }

  // ── 元数据缓存 ────────────────────────────────────

  Future<void> _ensureMeta() async {
    if (_metaFetched) return;

    // 发 range(0,0) 请求拿元数据帧
    final stream = await _fetchStreamRange(0, 0);
    // 只消费到首个数据帧：meta 帧解析在数据帧前完成
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
  Future<Stream<Uint8List>> _fetchStreamRange(
    int rangeStart,
    int rangeEnd,
  ) async {
    // 构建请求体
    final payload = {
      'token': jwtToken,
      'disk_id': diskId,
      'path': path,
      'filename': filename,
      'range_start': rangeStart,
      'range_end': rangeEnd,
    };
    if (repairTaskId > 0) {
      payload['repair_task_id'] = repairTaskId;
    }
    final plainBytes = utf8.encode(jsonEncode(payload));
    final envelope = CryptoUtils.encryptEnvelope(sessionKey, plainBytes);

    final dio = Dio(
      BaseOptions(
        connectTimeout: const Duration(seconds: 5),
        // ⚠ 这里**不能**用 30 秒级别的 receiveTimeout（v1.5.0 反馈修复）：
        // 我们已经在 `_handleGet` 里对响应做 flush 背压，而背压会在**播放器缓冲满**时
        // 主动暂停读取上游 —— 用户暂停播放、或播放器缓冲吃满时，服务端本来就会
        // 长时间没有新数据，30 秒的「两帧之间超时」会误判成连接故障并中断。
        // 保留一个较长的上限只为兜住"服务端彻底挂死"：正常暂停不会触发。
        receiveTimeout: const Duration(minutes: 5),
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
      // 尝试从加密信封解出 detail（含 [MEDIA_NEEDS_REPAIR] 错误码）
      String detail = '服务端返回错误: ${resp.statusCode}';
      try {
        final raw = resp.data;
        final body = raw is String ? jsonDecode(raw) : raw;
        if (body is Map<String, dynamic> &&
            body['nonce'] != null &&
            body['ciphertext'] != null) {
          final plain = CryptoUtils.decryptEnvelope(
            sessionKey,
            body['nonce'] as String,
            body['ciphertext'] as String,
          );
          final obj = jsonDecode(utf8.decode(plain));
          detail = obj['detail']?.toString() ?? detail;
        }
      } catch (_) {}
      throw Exception(detail);
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
