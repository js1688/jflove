import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'app.dart';
import 'providers/theme_provider.dart';
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

  // 0. 主题偏好（v1.5.0）：在 runApp 之前恢复，保证首帧就是正确主题，
  //    不会出现「先亮后暗」的闪烁
  //
  //    同时关闭 Riverpod 3 的默认自动重试（`ProviderContainer.defaultRetry`：
  //    最多 10 次、指数退避到 6.4s）。它会让一个失败请求在约 30 秒内处于
  //    「loading + 已有 error」状态，`AsyncValue.when` 于是持续走 loading 分支，
  //    用户看到的是骨架屏而不是错误提示 —— 网络不通时体验极差。
  //    改为：失败立即呈现 `ErrorState`，由用户点「重试」再发起请求。
  final container = ProviderContainer(retry: (_, _) => null);
  await container.read(themeModeProvider.notifier).load();

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

  runApp(UncontrolledProviderScope(
    container: container,
    child: const JFLoveApp(),
  ));
}
