import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

/// 本地流代理的**背压**回归测试（v1.5.0 反馈修复）
///
/// 用户报的现象很反直觉：**手机移动网络（IPv6，慢）播 1GB 大视频正常，
/// 换内网地址（局域网，快）播同一个视频反而卡住、卡死**。
///
/// 根因就在 `lib/utils/stream_proxy.dart` 的转发循环：
///
/// ```dart
/// await for (final chunk in stream) {
///   resp.add(chunk);   // ← 非阻塞，只是往 HttpResponse 的内存缓冲里塞
/// }
/// ```
///
/// `HttpResponse.add()` **不阻塞**，而 `HttpResponse` 的缓冲是无界的：
///   · 播放器只按播放速率取数据，取不走的部分全堆在内存里 —— 上游越快堆得越猛，
///     1GB 的视频足以吃满内存、GC 抖动到界面卡死；
///   · 这条循环还跑在**主 isolate** 上（每 64KB 一帧的纯 Dart ChaCha20-Poly1305 解密），
///     上游不停就永远不让出事件循环 → 界面直接失去响应。
/// 慢链路自带节流，所以移动网络反而正常 —— 快的链路才把问题暴露出来。
///
/// 修法是**逐块 `await resp.flush()`**：flush 会等到数据真正被 socket 接收，
/// 背压因此一路传导回上游（播放器不取就不下载、不解密），内存与上游速率解耦。
///
/// 这里用源码级断言锁住这条（真实的播放行为需要真机 + 大文件，属冒烟环节；
/// 但"有没有背压"是代码结构问题，可以在单元层钉死）。
void main() {
  const proxyPath = 'lib/utils/stream_proxy.dart';
  late String src;

  setUpAll(() {
    src = File(proxyPath).readAsStringSync();
  });

  /// 去掉行注释后再断言：注释里会**提到**被否决的写法（讲清为什么不能用），
  /// 直接对原文做子串断言会被自己的注释绊倒。
  String codeOnly(String s) => s
      .split('\n')
      .where((line) => !line.trimLeft().startsWith('//'))
      .join('\n');

  group('转发循环必须对响应施加背压', () {
    test('每个 chunk 之后都要 flush（否则内存无界增长）', () {
      final code = codeOnly(src);
      final addAt = code.indexOf('resp.add(chunk);');
      expect(addAt, greaterThan(-1), reason: '找不到 resp.add(chunk)');
      final after = code.substring(addAt, addAt + 120);
      expect(
        after.contains('await resp.flush();'),
        isTrue,
        reason: 'resp.add(chunk) 之后必须 await resp.flush()，否则快速上游会把内存吃光',
      );
    });

    test('不得存在"add 了但整段循环不 flush"的写法', () {
      final code = codeOnly(src);
      // 循环体里必须同时出现 add 与 flush
      final loopAt = code.indexOf('await for (final chunk in stream) {');
      expect(loopAt, greaterThan(-1), reason: '找不到转发循环');
      final loopBody = code.substring(loopAt, code.indexOf('}\n', loopAt) + 2);
      expect(loopBody.contains('resp.add(chunk);'), isTrue);
      expect(loopBody.contains('await resp.flush();'), isTrue,
          reason: '转发循环内缺少 flush —— 局域网（快）播大视频会卡死');
    });
  });

  group('接收超时不能误伤"背压造成的正常暂停"', () {
    test('receiveTimeout 不再用 30 秒级（背压会主动暂停读上游）', () {
      final code = codeOnly(src);
      expect(
        code.contains('receiveTimeout: const Duration(seconds: 30)'),
        isFalse,
        reason: '背压会在播放器缓冲满时暂停读上游，30 秒的帧间超时会误判成连接故障',
      );
      expect(code.contains('receiveTimeout: const Duration(minutes: 5)'), isTrue);
      // 连接建立仍要有超时兜底
      expect(code.contains('connectTimeout: const Duration(seconds: 5)'), isTrue);
    });
  });

  group('错误路径不得把响应悬着', () {
    test('客户端断开/异常时也要 close 响应', () {
      final code = codeOnly(src);
      final catchAt = code.indexOf('if (e is HttpException || e is SocketException');
      expect(catchAt, greaterThan(-1), reason: '找不到客户端断开的处理分支');
      final branch = code.substring(catchAt, catchAt + 260);
      expect(
        branch.contains('request.response.close()'),
        isTrue,
        reason: '该分支必须关闭响应，否则连接一直挂着',
      );
    });
  });
}
