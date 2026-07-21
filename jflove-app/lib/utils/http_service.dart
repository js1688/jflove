import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';

import '../config/app_config.dart';
import 'crypto.dart';
import 'exception.dart';
import 'session.dart';
import 'stream_frame.dart';

/// HTTP 服务（加密通信层）
///
/// 对标 jflove-desktop/src/utils/http_client.py。
class HttpService {
  late final Dio _dio;
  final SessionManager _session;

  HttpService(this._session) {
    _dio = Dio(
      BaseOptions(
        connectTimeout: const Duration(seconds: AppConfig.connectTimeout),
        receiveTimeout: const Duration(seconds: AppConfig.receiveTimeout),
        headers: {'Content-Type': 'application/json'},
      ),
    );

    _dio.interceptors.add(
      InterceptorsWrapper(
        onRequest: (options, handler) {
          if (kDebugMode) {
            // ignore: avoid_print
            print('[HTTP] ${options.method} ${options.path}');
          }
          handler.next(options);
        },
      ),
    );
  }

  Map<String, dynamic> _buildPayload(Map<String, dynamic>? data) {
    final payload = <String, dynamic>{};
    if (_session.token.isNotEmpty) {
      payload['token'] = _session.token;
    }
    if (data != null) {
      payload.addAll(data);
    }
    return payload;
  }

  Map<String, dynamic>? _decryptEnvelope(Map<String, dynamic> body) {
    try {
      if (body.containsKey('nonce') && body.containsKey('ciphertext')) {
        if (_session.sessionKey == null) return null;
        final plainBytes = CryptoUtils.decryptEnvelope(
          _session.sessionKey!,
          body['nonce'] as String,
          body['ciphertext'] as String,
        );
        return jsonDecode(utf8.decode(plainBytes)) as Map<String, dynamic>;
      }
      return body;
    } catch (_) {
      return null;
    }
  }

  String _extractErrorDetail(DioException e) {
    try {
      if (e.response?.data is Map) {
        final data = e.response!.data as Map<String, dynamic>;
        final decrypted = _decryptEnvelope(data);
        if (decrypted != null && decrypted.containsKey('detail')) {
          return decrypted['detail'] as String;
        }
        if (data.containsKey('detail')) return data['detail'] as String;
      }
    } catch (_) {}
    return e.message ?? '未知错误';
  }

  bool _isEcdhSessionError(String detail) {
    return detail.contains('会话') && detail.contains('过期') ||
        detail.contains('session') && detail.contains('invalid') ||
        detail.contains('密钥') && detail.contains('不存在');
  }

  Future<bool> _tryResyncEcdh() async {
    try {
      final kp = CryptoUtils.generateKeyPair();
      final resp = await _dio.post(
        '${_session.serverUrl}/api/v1/auth/key-exchange',
        data: {'client_public_key': kp.publicKeyB64},
      );
      final data = resp.data as Map<String, dynamic>;
      final serverPublicKeyB64 = data['server_public_key'] as String;
      final newSessionId = data['session_id'] as String;

      _session.sessionKey = CryptoUtils.deriveSessionKey(
        kp.privateKeyRaw,
        serverPublicKeyB64,
      );
      _session.sessionId = newSessionId;
      _session.keyExchangeTime = DateTime.now().millisecondsSinceEpoch / 1000;
      return true;
    } catch (_) {
      return false;
    }
  }

  /// 加密请求（返回解密后的 Map）
  Future<Map<String, dynamic>> _encryptedRequest(
    String method,
    String path,
    Map<String, dynamic>? data,
  ) async {
    try {
      return await _doEncryptedRequest(method, path, data);
    } on DioException catch (e) {
      final detail = _extractErrorDetail(e);
      if (_isEcdhSessionError(detail) && await _tryResyncEcdh()) {
        return await _doEncryptedRequest(method, path, data);
      }
      // 将加密的错误响应转换为用户可读的异常
      throw AppException(code: e.response?.statusCode ?? -1, message: detail);
    }
  }

  Future<Map<String, dynamic>> _doEncryptedRequest(
    String method,
    String path,
    Map<String, dynamic>? data,
  ) async {
    final payload = _buildPayload(data);
    final plainBytes = utf8.encode(jsonEncode(payload));
    final envelope = CryptoUtils.encryptEnvelope(
      _session.sessionKey!,
      plainBytes,
    );

    Response resp;
    final url = '${_session.serverUrl}$path';
    final body = {'nonce': envelope.nonce, 'ciphertext': envelope.ciphertext};
    final opts = Options(headers: {'X-Session-ID': _session.sessionId});

    switch (method.toUpperCase()) {
      case 'POST':
        resp = await _dio.post(url, data: body, options: opts);
        break;
      case 'GET':
        resp = await _dio.get(url, data: body, options: opts);
        break;
      case 'PUT':
        resp = await _dio.put(url, data: body, options: opts);
        break;
      case 'DELETE':
        resp = await _dio.delete(url, data: body, options: opts);
        break;
      default:
        throw ArgumentError('不支持的 HTTP 方法: $method');
    }

    final respData = resp.data as Map<String, dynamic>;
    final decrypted = _decryptEnvelope(respData);
    if (decrypted != null) return decrypted;
    throw AppException.serverError('响应解密失败');
  }

  // ---- 公开接口 ----

  Future<Map<String, dynamic>> plainPost(
    String path,
    Map<String, dynamic> body,
  ) async {
    final resp = await _dio.post('${_session.serverUrl}$path', data: body);
    return resp.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> plainGet(String path) async {
    final resp = await _dio.get('${_session.serverUrl}$path');
    return resp.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> encryptedPost(
    String path,
    Map<String, dynamic>? data,
  ) => _encryptedRequest('POST', path, data);

  Future<Map<String, dynamic>> encryptedGet(
    String path,
    Map<String, dynamic>? data,
  ) => _encryptedRequest('GET', path, data);

  Future<Map<String, dynamic>> encryptedPut(
    String path,
    Map<String, dynamic>? data,
  ) => _encryptedRequest('PUT', path, data);

  Future<Map<String, dynamic>> encryptedDelete(
    String path,
    Map<String, dynamic>? data,
  ) => _encryptedRequest('DELETE', path, data);

  /// 流式下载（边收边解密，经 StreamFrameParser 逐帧解析）
  Future<Stream<Uint8List>> encryptedDownloadStream(
    String path,
    Map<String, dynamic> data,
  ) async {
    final payload = _buildPayload(data);
    final plainBytes = utf8.encode(jsonEncode(payload));
    final envelope = CryptoUtils.encryptEnvelope(
      _session.sessionKey!,
      plainBytes,
    );

    try {
      final resp = await _dio.get(
        '${_session.serverUrl}$path',
        data: {'nonce': envelope.nonce, 'ciphertext': envelope.ciphertext},
        options: Options(
          headers: {'X-Session-ID': _session.sessionId},
          responseType: ResponseType.stream,
          // 接受所有状态码，手动检查避免 Dio 默认抛异常
          validateStatus: (_) => true,
        ),
      );

      if (resp.statusCode != null && resp.statusCode! >= 400) {
        // 尝试解密错误响应体获取可读错误信息
        String detail = '流式请求失败: ${resp.statusCode}';
        try {
          if (resp.data is Map) {
            final body = resp.data as Map<String, dynamic>;
            final decrypted = _decryptEnvelope(body);
            if (decrypted != null && decrypted.containsKey('detail')) {
              detail = decrypted['detail'] as String;
            }
          }
        } catch (_) {}

        // ECDH 重同步
        if (_isEcdhSessionError(detail) && await _tryResyncEcdh()) {
          return encryptedDownloadStream(path, data);
        }

        throw AppException(code: resp.statusCode!, message: detail);
      }

      final rawStream = resp.data.stream as Stream<List<int>>;
      // 通过 StreamFrameParser 逐帧解密输出
      return StreamFrameParser(
        sessionKey: _session.sessionKey!,
        rawStream: rawStream,
      ).parse();
    } on AppException {
      rethrow;
    } on DioException catch (e) {
      // Dio 网络层错误（连接超时、DNS 解析失败等）
      final detail = _extractErrorDetail(e);
      if (_isEcdhSessionError(detail) && await _tryResyncEcdh()) {
        return encryptedDownloadStream(path, data);
      }
      throw AppException(code: e.response?.statusCode ?? -1, message: detail);
    }
  }
}
