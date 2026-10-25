import 'dart:io';
import 'dart:isolate';
import 'dart:typed_data';

import 'package:pointycastle/digests/sha256.dart';
import 'package:pointycastle/export.dart' show SHA256Digest;

/// 同步计算文件 SHA256（十六进制小写）
///
/// 纯 CPU/IO 函数，**不要在主 isolate 直接对大文件调用**；
/// 生产路径统一走 [sha256FileInIsolate]。
String sha256FileSync(String path) {
  final digest = SHA256Digest();
  final file = File(path);
  final total = file.lengthSync();
  final raf = file.openSync();
  try {
    var done = 0;
    while (done < total) {
      final chunk = raf.readSync(1 << 20);
      if (chunk.isEmpty) break;
      digest.update(Uint8List.fromList(chunk), 0, chunk.length);
      done += chunk.length;
    }
  } finally {
    raf.closeSync();
  }
  final out = Uint8List(digest.digestSize);
  digest.doFinal(out, 0);
  return out.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
}

/// 在后台 isolate 计算文件 SHA256（大视频上传不阻塞 UI，v1.8.3 / F44）
Future<String> sha256FileInIsolate(String path) =>
    Isolate.run(() => sha256FileSync(path));
