import 'package:logging/logging.dart';
import 'package:flutter/foundation.dart';

/// JFLove 移动端日志工具
///
/// release 模式全静默，debug 模式输出到控制台。
final Logger log = Logger('jflove');

/// 初始化日志系统
void initLogger() {
  if (kDebugMode) {
    Logger.root.level = Level.INFO;
    Logger.root.onRecord.listen((record) {
      // ignore: avoid_print
      print('[${record.level.name}] ${record.time}: ${record.message}');
    });
  } else {
    Logger.root.level = Level.OFF;
  }
}
