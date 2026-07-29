import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'app.dart';
import 'utils/crypto.dart';
import 'utils/http_service.dart';
import 'utils/logger.dart';
import 'utils/session.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  initLogger();

  // 锁定竖屏
  SystemChrome.setPreferredOrientations([
    DeviceOrientation.portraitUp,
    DeviceOrientation.portraitDown,
  ]);

  // 1. 从安全存储恢复持久化字段（token / serverUrl / TTL 偏好等）
  final session = SessionManager();
  await session.loadFromStorage();

  // 2. 尝试自动登录（对标桌面端 try_restore_session）
  //    恢复条件：token 存在且距过期 ≥ 60 秒
  if (session.token.isNotEmpty && session.serverUrl.isNotEmpty) {
    final nowSec = DateTime.now().millisecondsSinceEpoch / 1000;
    if (session.tokenExpiresAt - nowSec >= 60) {
      try {
        final httpService = HttpService(session);

        // 重新执行 ECDH 密钥交换，恢复 sessionKey/sessionId
        final kp = CryptoUtils.generateKeyPair();
        final resp = await httpService.plainPost('/api/v1/auth/key-exchange', {
          'client_public_key': kp.publicKeyB64,
        });
        session.sessionKey = CryptoUtils.deriveSessionKey(
          kp.privateKeyRaw,
          resp['server_public_key'] as String,
        );
        session.sessionId = resp['session_id'] as String;
        session.keyExchangeTime = nowSec;
      } catch (_) {
        // 恢复失败（网络不通、服务器不可达等），静默回退到登录页
        // token 依然保留在内存中，只是本次没有恢复加密通道
      }
    }
  }

  runApp(const ProviderScope(child: JFLoveApp()));
}
