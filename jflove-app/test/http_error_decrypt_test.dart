import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';

import 'package:jflove_app/utils/crypto.dart';
import 'package:jflove_app/utils/exception.dart';
import 'package:jflove_app/utils/http_service.dart';
import 'package:jflove_app/utils/session.dart';

/// 移动端加密链路安全用例（§9.1 / §9.3 / §9.5 / §9.6）
///
/// 对应 `AGENTS.md §9.6` 要求 `testing` 必测的三类：
///   ① 加密信封往返（含**错误响应解密**路径）；
///   ③ 文件下载流可被客户端正确解密、**篡改后认证失败**（不吐半截数据）。
///
/// 做法：在回环地址（127.0.0.1）起一个真实的 `HttpServer` 当"服务端"，
/// 由它按服务端协议返回加密信封 —— 这样验证的是**真实的 Dio + 解密链路**，
/// 而不是把私有方法反射出来测。全程不访问外网、不写真实 token（§9.4）。
void main() {
  late HttpServer server;
  late String baseUrl;
  late SessionManager session;
  late Uint8List sessionKey;
  late HttpService http;

  /// 服务端收到的请求头与解密后的请求体（用于断言 token 位置与请求头内容）
  final capturedHeaders = <String, HttpHeaders>{};
  final capturedPayloads = <String, Map<String, dynamic>>{};

  /// 用测试期的固定假密钥（非真实会话密钥，仅本次进程内使用）
  Uint8List testKey() {
    final kp = CryptoUtils.generateKeyPair();
    final peer = CryptoUtils.generateKeyPair();
    return CryptoUtils.deriveSessionKey(kp.privateKeyRaw, peer.publicKeyB64);
  }

  Map<String, dynamic> envelope(Map<String, dynamic> payload) {
    final e = CryptoUtils.encryptEnvelope(sessionKey, utf8.encode(jsonEncode(payload)));
    return {'nonce': e.nonce, 'ciphertext': e.ciphertext};
  }

  Future<void> handle(HttpRequest req) async {
    final raw = await utf8.decoder.bind(req).join();
    final body = raw.isEmpty
        ? <String, dynamic>{}
        : jsonDecode(raw) as Map<String, dynamic>;
    final path = req.uri.path;
    capturedHeaders[path] = req.headers;

    // "服务端"解密请求体（协议要求：业务参数与 token 都在加密信封内）
    if (body['nonce'] != null && body['ciphertext'] != null) {
      final plain = CryptoUtils.decryptEnvelope(
        sessionKey,
        body['nonce'] as String,
        body['ciphertext'] as String,
      );
      capturedPayloads[path] = jsonDecode(utf8.decode(plain)) as Map<String, dynamic>;
    }

    final resp = req.response;
    resp.headers.contentType = ContentType.json;

    switch (path) {
      case '/api/v1/ok':
        resp.statusCode = 200;
        resp.write(jsonEncode(envelope({
          'message': '保存成功',
          'files': [
            {'name': '测试.txt', 'size': 12},
          ],
          'chunk': 'AAECAwQ=',
        })));
        break;

      case '/api/v1/err403':
        resp.statusCode = 403;
        resp.write(jsonEncode(envelope({'detail': '对目标磁盘没有读取权限'})));
        break;

      case '/api/v1/err422':
        resp.statusCode = 422;
        resp.write(jsonEncode(envelope({'detail': '参数校验失败'})));
        break;

      case '/api/v1/tampered':
        // 200 但密文被改动一个字节 → Poly1305 认证必须失败
        final env = envelope({'ok': true, 'secret': '不应被读出'});
        final bytes = base64Decode(env['ciphertext']!);
        bytes[0] ^= 0xFF;
        resp.statusCode = 200;
        resp.write(jsonEncode({
          'nonce': env['nonce'],
          'ciphertext': base64Encode(bytes),
        }));
        break;

      case '/api/v1/download':
        resp.statusCode = 200;
        resp.headers.contentType = ContentType.binary;
        final chunks = <List<int>>[
          utf8.encode('第一块：中文内容'),
          List<int>.generate(512, (i) => i % 256),
        ];
        for (final chunk in chunks) {
          resp.add(CryptoUtils.encryptStreamChunk(sessionKey, Uint8List.fromList(chunk)));
        }
        break;

      case '/api/v1/download-tampered':
        resp.statusCode = 200;
        resp.headers.contentType = ContentType.binary;
        final good = CryptoUtils.encryptStreamChunk(
          sessionKey,
          Uint8List.fromList(utf8.encode('正常分片')),
        );
        final bad = CryptoUtils.encryptStreamChunk(
          sessionKey,
          Uint8List.fromList(utf8.encode('被篡改的分片')),
        );
        bad[bad.length - 1] ^= 0x01; // 破坏认证标签
        resp.add(good);
        resp.add(bad);
        break;

      default:
        resp.statusCode = 404;
        resp.write(jsonEncode(envelope({'detail': '接口不存在'})));
    }
    await resp.close();
  }

  setUpAll(() async {
    server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    server.listen(handle);
    baseUrl = 'http://127.0.0.1:${server.port}';

    session = SessionManager();
    sessionKey = testKey();
    session.serverUrl = baseUrl;
    session.sessionId = 'sess-v150-test';
    session.sessionKey = sessionKey;
    session.token = 'jwt-v150-test';
    http = HttpService(session);
  });

  tearDownAll(() async {
    session.sessionKey = null;
    session.sessionId = '';
    session.token = '';
    session.serverUrl = '';
    await server.close(force: true);
  });

  setUp(() {
    capturedHeaders.clear();
    capturedPayloads.clear();
  });

  group('① 加密信封往返', () {
    test('成功响应解密后业务字段完整（含中文与二进制 base64）', () async {
      final data = await http.encryptedPost('/api/v1/ok', {'disk_id': 1});

      expect(data['message'], '保存成功');
      expect((data['files'] as List).first['name'], '测试.txt');
      expect(data['chunk'], 'AAECAwQ=');
    });

    test('错误响应（403/422）也是加密信封，客户端解出中文 detail', () async {
      for (final entry in {
        '/api/v1/err403': '对目标磁盘没有读取权限',
        '/api/v1/err422': '参数校验失败',
      }.entries) {
        try {
          await http.encryptedPost(entry.key, const {});
          fail('应当抛出 AppException');
        } on AppException catch (e) {
          expect(e.message, entry.value, reason: '${entry.key} 的 detail 未被解密');
          expect(e.code, entry.key.endsWith('403') ? 403 : 422);
        }
      }
    });

    test('密文被篡改：认证失败 → 不把半个数据当成功结果', () async {
      try {
        await http.encryptedPost('/api/v1/tampered', const {});
        fail('应当抛出 AppException');
      } on AppException catch (e) {
        expect(e.code, 500);
        expect(e.message, '响应解密失败');
      }
    });
  });

  group('§9.3：JWT 只走加密 body，不出现 Authorization 头', () {
    test('请求头只有 X-Session-ID，token 在解密后的 body 内', () async {
      await http.encryptedPost('/api/v1/ok', {'disk_id': 7});

      final headers = capturedHeaders['/api/v1/ok']!;
      expect(headers.value('X-Session-ID'), 'sess-v150-test');
      expect(headers.value('Authorization'), isNull);

      final payload = capturedPayloads['/api/v1/ok']!;
      expect(payload['token'], 'jwt-v150-test');
      expect(payload['disk_id'], 7);
    });

    test('GET 也把参数放加密 body（URL 上不出现业务参数）', () async {
      await http.encryptedGet('/api/v1/ok', {'path': 'a.txt'});

      expect(capturedPayloads['/api/v1/ok']!['path'], 'a.txt');
      expect(capturedPayloads['/api/v1/ok']!['token'], 'jwt-v150-test');
    });
  });

  group('③ 文件下载流', () {
    test('逐帧解密后字节与原始内容一致（含中文与二进制）', () async {
      final stream = await http.encryptedDownloadStream('/api/v1/download', {'path': 'a.bin'});
      final collected = <int>[];
      await for (final chunk in stream) {
        collected.addAll(chunk);
      }

      final expected = <int>[
        ...utf8.encode('第一块：中文内容'),
        ...List<int>.generate(512, (i) => i % 256),
      ];
      expect(collected, expected);
    });

    test('被篡改的帧不会吐出明文（认证失败即丢弃该帧）', () async {
      final stream =
          await http.encryptedDownloadStream('/api/v1/download-tampered', {'path': 'b.bin'});
      final collected = <int>[];
      await for (final chunk in stream) {
        collected.addAll(chunk);
      }

      final text = utf8.decode(collected, allowMalformed: true);
      expect(text, contains('正常分片'));
      expect(text.contains('被篡改的分片'), isFalse, reason: '篡改帧的明文不得出现');
    });

    test('下载请求同样只带 X-Session-ID，参数在加密 body 内', () async {
      final stream = await http.encryptedDownloadStream('/api/v1/download', {'path': 'c.bin'});
      await stream.drain<void>();

      final headers = capturedHeaders['/api/v1/download']!;
      expect(headers.value('X-Session-ID'), 'sess-v150-test');
      expect(headers.value('Authorization'), isNull);
      expect(capturedPayloads['/api/v1/download']!['path'], 'c.bin');
      expect(capturedPayloads['/api/v1/download']!['token'], 'jwt-v150-test');
    });
  });

  group('白名单边界（§9.1）', () {
    test('明文接口（key-exchange / admin-exists）不带加密信封，只在客户端封装内调用', () {
      final source = File('lib/utils/http_service.dart').readAsStringSync();
      // 明文方法存在但仅两个：plainPost / plainGet
      expect(source.contains('plainPost'), isTrue);
      expect(source.contains('plainGet'), isTrue);
      // 业务方法一律走加密：不允许出现裸 _dio.post('/api/v1/...')
      final offenders = RegExp(r"_dio\.(post|get|put|delete)\(\s*'/api")
          .allMatches(source)
          .map((m) => m.group(0)!)
          .toList();
      expect(offenders, isEmpty, reason: '业务请求不得绕过加密封装：$offenders');
    });

    test('明文调用点只出现在 auth_service 的白名单端点', () {
      final offenders = <String>[];
      for (final entity in Directory('lib').listSync(recursive: true)) {
        if (entity is! File || !entity.path.endsWith('.dart')) continue;
        final text = entity.readAsStringSync();
        for (final m in RegExp(r"\.(?:plainPost|plainGet)\(\s*'([^']+)'").allMatches(text)) {
          final path = m.group(1)!;
          if (!path.startsWith('/api/v1/auth/key-exchange') &&
              !path.startsWith('/api/v1/auth/admin-exists') &&
              path != '/health') {
            offenders.add('${entity.path}: $path');
          }
        }
      }
      expect(offenders, isEmpty, reason: '出现明文白名单以外的调用点：$offenders');
    });
  });
}
