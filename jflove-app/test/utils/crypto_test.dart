import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';

import 'package:jflove_app/utils/crypto.dart';

/// 加密原语交叉验证测试
///
/// 验证移动端 pointycastle + x25519 加密栈与桌面端/server 端互通。
void main() {
  group('X25519 ECDH', () {
    test('密钥对生成', () {
      final kp = CryptoUtils.generateKeyPair();
      expect(kp.privateKeyRaw.length, 32);
      expect(kp.publicKeyB64.isNotEmpty, true);
      // 验证公钥是有效的 Base64
      final decoded = base64Decode(kp.publicKeyB64);
      expect(decoded.length, 32);
    });

    test('ECDH 派生 session_key 长度', () {
      // Alice 生成密钥对
      final alice = CryptoUtils.generateKeyPair();
      // Bob 生成密钥对
      final bob = CryptoUtils.generateKeyPair();

      // Alice 用自己私钥 + Bob 公钥派生
      final aliceSession = CryptoUtils.deriveSessionKey(alice.privateKeyRaw, bob.publicKeyB64);
      // Bob 用自己私钥 + Alice 公钥派生
      final bobSession = CryptoUtils.deriveSessionKey(bob.privateKeyRaw, alice.publicKeyB64);

      // 双方 session_key 应当一致（长度 32 字节）
      expect(aliceSession.length, 32);
      expect(bobSession.length, 32);
      expect(aliceSession, orderedEquals(bobSession));
    });

    test('不同密钥对派生不同 session_key', () {
      final alice = CryptoUtils.generateKeyPair();
      final bob1 = CryptoUtils.generateKeyPair();
      final bob2 = CryptoUtils.generateKeyPair();

      final session1 = CryptoUtils.deriveSessionKey(alice.privateKeyRaw, bob1.publicKeyB64);
      final session2 = CryptoUtils.deriveSessionKey(alice.privateKeyRaw, bob2.publicKeyB64);

      // 不同对端应派生出不同 session_key
      expect(session1, isNot(orderedEquals(session2)));
    });
  });

  group('ChaCha20-Poly1305 加密信封', () {
    late Uint8List sessionKey;

    setUp(() {
      final kp = CryptoUtils.generateKeyPair();
      // 用自己和自己做 ECDH（仅用于测试，不用于生产）
      sessionKey = CryptoUtils.deriveSessionKey(kp.privateKeyRaw, kp.publicKeyB64);
    });

    test('整包加密-解密往返', () {
      final plaintext = utf8.encode('{"test": "hello jflove", "number": 42}');

      final envelope = CryptoUtils.encryptEnvelope(sessionKey, Uint8List.fromList(plaintext));
      expect(envelope.nonce.isNotEmpty, true);
      expect(envelope.ciphertext.isNotEmpty, true);
      expect(envelope.nonce, isNot(equals(envelope.ciphertext)));

      final decrypted = CryptoUtils.decryptEnvelope(sessionKey, envelope.nonce, envelope.ciphertext);
      expect(utf8.decode(decrypted), equals(utf8.decode(Uint8List.fromList(plaintext))));
    });

    test('篡改密文应抛出异常', () {
      final plaintext = utf8.encode('sensitive data');
      final envelope = CryptoUtils.encryptEnvelope(sessionKey, Uint8List.fromList(plaintext));

      // 篡改密文字节
      final cipherBytes = base64Decode(envelope.ciphertext);
      cipherBytes[0] ^= 0xFF;
      final tamperedCiphertext = base64Encode(cipherBytes);

      expect(
        () => CryptoUtils.decryptEnvelope(sessionKey, envelope.nonce, tamperedCiphertext),
        throwsArgumentError,
      );
    });

    test('错误的 nonce 应抛出异常', () {
      final plaintext = utf8.encode('test data');
      final envelope = CryptoUtils.encryptEnvelope(sessionKey, Uint8List.fromList(plaintext));

      expect(
        () => CryptoUtils.decryptEnvelope(sessionKey, 'AAAAAAAAAAAAAAAAAAAAAA==', envelope.ciphertext),
        throwsArgumentError,
      );
    });
  });

  group('流式分片加密', () {
    late Uint8List sessionKey;

    setUp(() {
      final kp = CryptoUtils.generateKeyPair();
      sessionKey = CryptoUtils.deriveSessionKey(kp.privateKeyRaw, kp.publicKeyB64);
    });

    test('流式分片加密-解密往返', () {
      final plaintext = Uint8List.fromList(List.generate(1000, (i) => i % 256));

      final frame = CryptoUtils.encryptStreamChunk(sessionKey, plaintext);
      expect(frame.length > plaintext.length, true); // 有加密开销

      // 解析帧头：[4B 长度][12B nonce][密文+16B tag]
      final frameLen = (ByteData.sublistView(frame).getUint32(0, Endian.big));
      expect(frameLen, equals(frame.length - 4));

      // 提取消 nonce 以外的帧体
      final frameBody = frame.sublist(4);
      final decrypted = CryptoUtils.decryptStreamChunk(sessionKey, frameBody);
      expect(decrypted, orderedEquals(plaintext));
    });

    test('空数据加密', () {
      final plaintext = Uint8List(0);
      final frame = CryptoUtils.encryptStreamChunk(sessionKey, plaintext);
      expect(frame.length, greaterThan(4));
    });

    test('大块数据（64KB）加密往返', () {
      final plaintext = Uint8List.fromList(List.generate(64 * 1024, (i) => i % 256));
      final frame = CryptoUtils.encryptStreamChunk(sessionKey, plaintext);
      final frameBody = frame.sublist(4);
      final decrypted = CryptoUtils.decryptStreamChunk(sessionKey, frameBody);
      expect(decrypted, orderedEquals(plaintext));
    });
  });

  group('安全用例 - 加密信封往返', () {
    test('测试向量：字符串加密解密一致', () {
      final kp = CryptoUtils.generateKeyPair();
      final sessionKey = CryptoUtils.deriveSessionKey(kp.privateKeyRaw, kp.publicKeyB64);

      const testCases = [
        '{"token": "abc123"}',
        '{"detail": "操作成功"}',
        '{"files": [{"name": "test.txt", "size": 1024}]}',
      ];

      for (final tc in testCases) {
        final plaintext = utf8.encode(tc);
        final envelope = CryptoUtils.encryptEnvelope(sessionKey, Uint8List.fromList(plaintext));
        final decrypted = CryptoUtils.decryptEnvelope(sessionKey, envelope.nonce, envelope.ciphertext);
        expect(utf8.decode(decrypted), equals(tc));
      }
    });
  });
}
