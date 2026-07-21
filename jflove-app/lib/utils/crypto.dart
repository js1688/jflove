import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:pointycastle/export.dart';
import 'package:x25519/x25519.dart' as x25519;

import '../config/app_config.dart';

/// JFLove 移动端加密工具
class CryptoUtils {
  CryptoUtils._();

  static ({Uint8List privateKeyRaw, String publicKeyB64}) generateKeyPair() {
    final kp = x25519.generateKeyPair();
    return (
      privateKeyRaw: Uint8List.fromList(kp.privateKey),
      publicKeyB64: base64Encode(kp.publicKey),
    );
  }

  static Uint8List deriveSessionKey(
    Uint8List privateKeyRaw,
    String peerPublicKeyB64,
  ) {
    final peerBytes = base64Decode(peerPublicKeyB64);
    final sharedSecret = x25519.X25519(
      privateKeyRaw.toList(),
      peerBytes.toList(),
    );
    final hkdf = HKDFKeyDerivator(SHA256Digest());
    hkdf.init(
      HkdfParameters(sharedSecret, 32, utf8.encode(AppConfig.cryptoSalt)),
    );
    // 使用空 data 调用 deriveKey，避免 sharedSecret 被追加到 info 字段
    final out = Uint8List(32);
    hkdf.deriveKey(null, 0, out, 0);
    return out;
  }

  /// ChaCha20-Poly1305 整包加密信封
  static ({String nonce, String ciphertext}) encryptEnvelope(
    Uint8List sessionKey,
    Uint8List plaintext,
  ) {
    final nonce = generateNonce();
    // 缓冲区：输入长度 + MAC + 安全余量
    final output = Uint8List(plaintext.length + 16 + 64);

    final cipher = ChaCha20Poly1305(ChaCha7539Engine(), Poly1305());
    cipher.init(
      true,
      AEADParameters(KeyParameter(sessionKey), 128, nonce, Uint8List(0)),
    );

    // processBytes 写入全块，返回已写字节数
    var written = cipher.processBytes(
      plaintext,
      0,
      plaintext.length,
      output,
      0,
    );
    // doFinal 写入剩余数据和 MAC 标签，返回追加字节数
    written += cipher.doFinal(output, written);

    final result = Uint8List.sublistView(output, 0, written);
    return (nonce: base64Encode(nonce), ciphertext: base64Encode(result));
  }

  /// ChaCha20-Poly1305 整包解密
  static Uint8List decryptEnvelope(
    Uint8List sessionKey,
    String nonceB64,
    String ciphertextB64,
  ) {
    final nonce = base64Decode(nonceB64);
    final ciphertext = base64Decode(ciphertextB64);
    final output = Uint8List(ciphertext.length + 64);

    final cipher = ChaCha20Poly1305(ChaCha7539Engine(), Poly1305());
    cipher.init(
      false,
      AEADParameters(KeyParameter(sessionKey), 128, nonce, Uint8List(0)),
    );

    var written = cipher.processBytes(
      ciphertext,
      0,
      ciphertext.length,
      output,
      0,
    );
    written += cipher.doFinal(output, written);

    return Uint8List.sublistView(output, 0, written);
  }

  /// 流式分片加密
  static Uint8List encryptStreamChunk(
    Uint8List sessionKey,
    Uint8List plaintext,
  ) {
    final nonce = generateNonce();
    final output = Uint8List(plaintext.length + 16 + 64);

    final cipher = ChaCha20Poly1305(ChaCha7539Engine(), Poly1305());
    cipher.init(
      true,
      AEADParameters(KeyParameter(sessionKey), 128, nonce, Uint8List(0)),
    );

    var written = cipher.processBytes(
      plaintext,
      0,
      plaintext.length,
      output,
      0,
    );
    written += cipher.doFinal(output, written);

    final cipherResult = Uint8List.sublistView(output, 0, written);

    // 构建帧：[4B 长度][nonce][密文+16B tag]
    final frameLen = nonce.length + cipherResult.length;
    final frame = Uint8List(4 + frameLen);
    final bd = ByteData.sublistView(frame);
    bd.setUint32(0, frameLen, Endian.big);
    frame.setRange(4, 4 + nonce.length, nonce);
    frame.setRange(4 + nonce.length, 4 + frameLen, cipherResult);
    return frame;
  }

  /// 流式分片解密
  static Uint8List decryptStreamChunk(
    Uint8List sessionKey,
    Uint8List frameBody,
  ) {
    if (frameBody.length < 12) throw ArgumentError('帧数据长度不足');

    final nonce = frameBody.sublist(0, 12);
    final ciphertext = frameBody.sublist(12);
    final output = Uint8List(ciphertext.length + 64);

    final cipher = ChaCha20Poly1305(ChaCha7539Engine(), Poly1305());
    cipher.init(
      false,
      AEADParameters(KeyParameter(sessionKey), 128, nonce, Uint8List(0)),
    );

    var written = cipher.processBytes(
      ciphertext,
      0,
      ciphertext.length,
      output,
      0,
    );
    written += cipher.doFinal(output, written);
    return Uint8List.sublistView(output, 0, written);
  }

  static Future<Uint8List> readExact(Stream<List<int>> raw, int n) async {
    final buffer = BytesBuilder();
    await for (final chunk in raw) {
      buffer.add(chunk);
      if (buffer.length >= n) {
        break;
      }
    }
    if (buffer.length < n) {
      throw StateError('流已提前结束，期望 $n 字节，实际 ${buffer.length} 字节');
    }
    return buffer.takeBytes().sublist(0, n);
  }

  static Future<Uint8List?> parseStreamFrame(
    Stream<List<int>> raw,
    Uint8List sessionKey,
  ) async {
    try {
      final lenBytes = await readExact(raw, 4);
      final frameLen = ByteData.sublistView(lenBytes).getUint32(0, Endian.big);
      if (frameLen == 0) return null;
      final frameBody = await readExact(raw, frameLen);
      return decryptStreamChunk(sessionKey, frameBody);
    } catch (_) {
      return null;
    }
  }

  static Uint8List generateNonce() {
    final random = Random.secure();
    final nonce = Uint8List(AppConfig.nonceLength);
    for (var i = 0; i < nonce.length; i++) {
      nonce[i] = random.nextInt(256);
    }
    return nonce;
  }
}
