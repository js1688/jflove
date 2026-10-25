import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:jflove_app/utils/file_hash.dart';

/// v1.8.3 / F44：SHA256 计算与后台 isolate 结果必须一致
void main() {
  test('sha256FileSync 与已知向量一致', () {
    final dir = Directory.systemTemp.createTempSync('file_hash_test_');
    addTearDown(() => dir.deleteSync(recursive: true));
    final file = File('${dir.path}/abc.bin')..writeAsBytesSync('abc'.codeUnits);

    expect(
      sha256FileSync(file.path),
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    );
  });

  test('sha256FileInIsolate 与大文件分块结果一致', () async {
    final dir = Directory.systemTemp.createTempSync('file_hash_test_');
    addTearDown(() => dir.deleteSync(recursive: true));
    final file = File('${dir.path}/big.bin')
      ..writeAsBytesSync(List<int>.generate(5 * 1024 * 1024, (i) => i % 251));

    final syncHash = sha256FileSync(file.path);
    final isolateHash = await sha256FileInIsolate(file.path);

    expect(isolateHash, syncHash);
    expect(isolateHash.length, 64);
  });
}
