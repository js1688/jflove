import 'dart:typed_data';

import 'crypto.dart';

/// 流式帧解析器
///
/// 对标 jflove-desktop/src/utils/crypto.py 的 parse_stream_frame。
/// 帧格式：[4B 大端长度][12B nonce][密文+16B tag]
class StreamFrameParser {
  final Uint8List sessionKey;
  final Stream<List<int>> rawStream;
  final BytesBuilder _buffer = BytesBuilder();

  StreamFrameParser({required this.sessionKey, required this.rawStream});

  /// 逐帧解密，返回解密后的明文流
  Stream<Uint8List> parse() async* {
    await for (final chunk in rawStream) {
      _buffer.add(chunk);

      // 循环处理缓冲区中所有完整帧
      while (_buffer.length >= 4) {
        // 读取 4 字节大端长度头（不取出）
        final currentBytes = _buffer.toBytes();
        final frameLen = ByteData.sublistView(
          currentBytes,
        ).getUint32(0, Endian.big);

        // 完整帧总大小 = 4B 头 + 帧体
        final totalLen = 4 + frameLen;
        if (_buffer.length < totalLen) break; // 不完整，等更多数据

        // 取出完整帧
        final allData = _buffer.takeBytes();
        final frame = allData.sublist(0, totalLen);

        // 剩余数据放回缓冲区
        if (allData.length > totalLen) {
          _buffer.add(allData.sublist(totalLen));
        }

        // 帧体 = 去掉 4B 头后的部分
        final frameBody = frame.sublist(4);
        try {
          yield CryptoUtils.decryptStreamChunk(sessionKey, frameBody);
        } catch (_) {
          // 解密失败，跳过此帧
          continue;
        }
      }
    }
  }
}
