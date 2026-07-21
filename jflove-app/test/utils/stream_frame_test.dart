import 'dart:async';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';

import 'package:jflove_app/utils/crypto.dart';
import 'package:jflove_app/utils/stream_frame.dart';

void main() {
  group('StreamFrameParser', () {
    late Uint8List sessionKey;

    setUp(() {
      final kp = CryptoUtils.generateKeyPair();
      sessionKey = kp.privateKeyRaw;
    });

    test('单帧加密-解析往返', () async {
      const plaintext = 'Hello JFLove!';
      final plainBytes = Uint8List.fromList(plaintext.codeUnits);
      final frame = CryptoUtils.encryptStreamChunk(sessionKey, plainBytes);

      final rawStream = Stream<List<int>>.value(frame);
      final parser = StreamFrameParser(
        sessionKey: sessionKey,
        rawStream: rawStream,
      );

      final results = <Uint8List>[];
      await for (final chunk in parser.parse()) {
        results.add(chunk);
      }

      expect(results.length, 1);
      expect(String.fromCharCodes(results[0]), plaintext);
    });

    test('分批接收多帧解析', () async {
      final payloads = ['A', 'BB', 'CCC'];

      final allBytes = <int>[];
      for (final p in payloads) {
        final bytes = Uint8List.fromList(p.codeUnits);
        final frame = CryptoUtils.encryptStreamChunk(sessionKey, bytes);
        allBytes.addAll(frame);
      }

      final controller = StreamController<List<int>>();
      final parser = StreamFrameParser(
        sessionKey: sessionKey,
        rawStream: controller.stream,
      );

      final futureResults = parser.parse().toList();

      final mid = allBytes.length ~/ 2;
      controller.add(allBytes.sublist(0, mid));
      controller.add(allBytes.sublist(mid));
      await controller.close();

      final results = await futureResults;
      expect(results.length, 3);
      expect(String.fromCharCodes(results[0]), 'A');
      expect(String.fromCharCodes(results[1]), 'BB');
      expect(String.fromCharCodes(results[2]), 'CCC');
    });

    test('篡改帧应被跳过', () async {
      const plaintext = 'Test data';
      final plainBytes = Uint8List.fromList(plaintext.codeUnits);
      final frame = CryptoUtils.encryptStreamChunk(sessionKey, plainBytes);

      final tampered = Uint8List.fromList(frame)..[frame.length - 1] ^= 0x01;

      final rawStream = Stream<List<int>>.value(tampered);
      final parser = StreamFrameParser(
        sessionKey: sessionKey,
        rawStream: rawStream,
      );

      final results = <Uint8List>[];
      await for (final chunk in parser.parse()) {
        results.add(chunk);
      }
      expect(results, isEmpty);
    });
  });
}
