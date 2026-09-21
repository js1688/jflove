import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../config/design_tokens.dart';
import '../../providers/session_provider.dart';
import '../../services/server_history_service.dart';
import '../../widgets/app_card.dart';

/// 登录页
///
/// 对标桌面端 login_window.py。
/// 功能：服务器地址 + 历史记录 + 密钥交换 + 管理员初始化 + 用户登录 + TTL 选择。
///
/// v1.5.0：唯一的「品牌时刻」页面 —— 背景改品牌光晕 + 极淡网格
/// （对标 Web 端 `AuthLayout` 的多层径向渐变），表单主体收进统一 `AppCard`，
/// Logo 用 `t.gradBrand` 渐变块，标题用渐变字，主按钮用 `PrimaryGradientButton`。
/// **登录逻辑（服务器地址历史、密钥交换、管理员初始化、错误文案）与 v1.4.2 完全一致。**
class LoginPage extends ConsumerStatefulWidget {
  const LoginPage({super.key});

  @override
  ConsumerState<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends ConsumerState<LoginPage> {
  final _serverController = TextEditingController();
  final _usernameController = TextEditingController();
  final _passwordController = TextEditingController();
  int _selectedTtl = 2592000; // 默认 30 天（与桌面端一致）
  bool _isLoading = false;
  String? _errorMsg;
  List<String> _serverHistory = [];

  static const _ttlOptions = {'1 天': 86400, '7 天': 604800, '30 天': 2592000};

  @override
  void initState() {
    super.initState();
    _restoreUserPreferences();
  }

  /// 恢复用户偏好：服务器地址、登录有效期
  Future<void> _restoreUserPreferences() async {
    final session = ref.read(sessionManagerProvider);
    final historyService = ServerHistoryService(session);

    // 1. 预填充上次成功连接的服务器地址（对标桌面端 get_default）
    if (session.serverUrl.isNotEmpty) {
      _serverController.text = session.serverUrl;
    } else {
      _serverController.text = 'http://localhost:8989';
    }

    // 2. 恢复登录有效期偏好（对标桌面端 load_local_session_max_seconds）
    if (session.localSessionMaxSeconds > 0) {
      _selectedTtl = session.localSessionMaxSeconds;
    }

    // 3. 预填充用户名（方便用户，安全：密码不预填充）
    if (session.username.isNotEmpty) {
      _usernameController.text = session.username;
    }

    // 4. 加载服务器历史
    if (mounted) {
      setState(() => _serverHistory = historyService.history);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    // 品牌渐变上的文字 / 图标统一取 `onPrimary`（主题里即白色），
    // 保证亮暗两种主题下都能在渐变底上保持对比度。
    final onBrand = Theme.of(context).colorScheme.onPrimary;

    return Scaffold(
      // 无 AppBar：品牌光晕直接铺满整屏，与 Web 端登录页的居中卡片一致
      body: _BrandBackdrop(
        child: SafeArea(
          child: Center(
            child: SingleChildScrollView(
              padding: EdgeInsets.all(t.s4),
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 420),
                // 外阴影层（卡片自身是描边 + 极浅阴影，这里补一层品牌时刻的浮起感）
                child: Container(
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(t.rLg),
                    boxShadow: t.e3,
                  ),
                  child: AppCard(
                    padding: EdgeInsets.all(t.s6),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        // ── 品牌区：渐变 Logo + 渐变字标题 ──
                        Container(
                          width: 64,
                          height: 64,
                          alignment: Alignment.center,
                          decoration: BoxDecoration(
                            gradient: t.gradBrand,
                            borderRadius: BorderRadius.circular(t.rXl),
                            boxShadow: t.e2,
                          ),
                          child: Icon(
                            Icons.shield_outlined,
                            size: 30,
                            color: onBrand,
                          ),
                        ),
                        SizedBox(height: t.s3),
                        ShaderMask(
                          blendMode: BlendMode.srcIn,
                          shaderCallback: (bounds) =>
                              t.gradBrand.createShader(bounds),
                          child: const Text(
                            'JFLove',
                            style: TextStyle(
                              fontSize: 24,
                              fontWeight: FontWeight.w700,
                              letterSpacing: -0.4,
                            ),
                          ),
                        ),
                        SizedBox(height: t.s1),
                        Text(
                          '私有文档 & 笔记管理',
                          style: TextStyle(fontSize: 11.5, color: t.fgSubtle),
                        ),
                        SizedBox(height: t.s6),

                        // 服务器地址（带历史下拉建议）
                        Autocomplete<String>(
                          optionsBuilder: (textEditingValue) {
                            if (textEditingValue.text.isEmpty) {
                              return _serverHistory;
                            }
                            return _serverHistory.where(
                              (addr) => addr.contains(textEditingValue.text),
                            );
                          },
                          onSelected: (value) => _serverController.text = value,
                          fieldViewBuilder:
                              (
                                context,
                                textEditingController,
                                focusNode,
                                onSubmitted,
                              ) {
                                // 同步 Autocomplete 的 controller 到 _serverController
                                _serverController.text =
                                    textEditingController.text;
                                return TextField(
                                  controller: textEditingController,
                                  focusNode: focusNode,
                                  decoration: const InputDecoration(
                                    labelText: '服务器地址',
                                    hintText: 'http://192.168.1.100:8989',
                                    prefixIcon: Icon(Icons.dns_outlined),
                                  ),
                                  onSubmitted: (_) => onSubmitted(),
                                  onChanged: (v) =>
                                      _serverController.text = v,
                                );
                              },
                        ),
                        SizedBox(height: t.s3),

                        // 用户名
                        TextField(
                          controller: _usernameController,
                          decoration: const InputDecoration(
                            labelText: '用户名',
                            prefixIcon: Icon(Icons.person_outline),
                          ),
                        ),
                        SizedBox(height: t.s3),

                        // 密码
                        TextField(
                          controller: _passwordController,
                          obscureText: true,
                          decoration: const InputDecoration(
                            labelText: '密码',
                            prefixIcon: Icon(Icons.lock_outline),
                          ),
                        ),
                        SizedBox(height: t.s3),

                        // Token 有效期
                        DropdownButtonFormField<int>(
                          initialValue: _selectedTtl,
                          decoration: const InputDecoration(
                            labelText: '登录有效期',
                            prefixIcon: Icon(Icons.timer_outlined),
                          ),
                          items: _ttlOptions.entries
                              .map(
                                (e) => DropdownMenuItem(
                                  value: e.value,
                                  child: Text(e.key),
                                ),
                              )
                              .toList(),
                          onChanged: (v) {
                            if (v != null) setState(() => _selectedTtl = v);
                          },
                        ),

                        // 错误提示（文案与 v1.4.2 一致，仅换成语义色令牌）
                        if (_errorMsg != null) ...[
                          SizedBox(height: t.s3),
                          Container(
                            padding: EdgeInsets.all(t.s3),
                            decoration: BoxDecoration(
                              color: AppTokens.danger500.withValues(
                                alpha: 0.10,
                              ),
                              borderRadius: BorderRadius.circular(t.rMd),
                              border: Border.all(
                                color: AppTokens.danger500.withValues(
                                  alpha: 0.28,
                                ),
                              ),
                            ),
                            child: Row(
                              children: [
                                const Icon(
                                  Icons.error_outline_rounded,
                                  size: 18,
                                  color: AppTokens.danger500,
                                ),
                                SizedBox(width: t.s2),
                                Expanded(
                                  child: Text(
                                    _errorMsg!,
                                    style: const TextStyle(
                                      fontSize: 12.5,
                                      height: 1.5,
                                      color: AppTokens.danger500,
                                    ),
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ],

                        SizedBox(height: t.s6),

                        // 登录按钮（品牌渐变主操作）
                        PrimaryGradientButton(
                          label: '登录',
                          icon: Icons.login_rounded,
                          loading: _isLoading,
                          expand: true,
                          onPressed: _isLoading ? null : _handleLogin,
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  Future<void> _handleLogin() async {
    final serverUrl = _serverController.text.trim();
    final username = _usernameController.text.trim();
    final password = _passwordController.text;

    if (serverUrl.isEmpty) {
      setState(() => _errorMsg = '请输入服务器地址');
      return;
    }
    if (username.isEmpty || password.isEmpty) {
      setState(() => _errorMsg = '请输入用户名和密码');
      return;
    }

    setState(() {
      _isLoading = true;
      _errorMsg = null;
    });

    try {
      final session = ref.read(sessionManagerProvider);
      session.serverUrl = serverUrl;

      final authService = ref.read(authServiceProvider);

      // 1. 密钥交换
      await authService.keyExchange();

      // 2. 检查管理员是否存在
      final exists = await authService.adminExists();

      if (!exists) {
        // 首次使用，注册管理员
        await authService.registerAdmin(username, password);
      }

      // 3. 登录（带 TTL）
      await authService.login(username, password, maxSeconds: _selectedTtl);

      // 4. 保存服务器地址到历史
      final historyService = ServerHistoryService(session);
      await historyService.addServer(serverUrl);

      if (mounted) context.go('/');
    } catch (e) {
      setState(
        () => _errorMsg = e.toString().replaceAll(RegExp(r'^Exception: '), ''),
      );
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  @override
  void dispose() {
    _serverController.dispose();
    _usernameController.dispose();
    _passwordController.dispose();
    super.dispose();
  }
}

/// 品牌背景（对标 Web 端 `AuthLayout` 的多层径向渐变光晕 + 极淡网格）
///
/// 三层径向光晕全部取令牌语义色（品牌 / 强调 / 信息），透明度压到很低，
/// 避免抢走表单卡片的注意力；网格只用 `borderSubtle`，亮暗主题自动跟随。
class _BrandBackdrop extends StatelessWidget {
  const _BrandBackdrop({required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    // 渐变的收尾色：画布色的完全透明版，避免引入颜色字面量
    final fade = t.bgCanvas.withValues(alpha: 0);

    return Stack(
      fit: StackFit.expand,
      children: [
        DecoratedBox(
          decoration: BoxDecoration(
            color: t.bgCanvas,
            gradient: RadialGradient(
              center: const Alignment(-0.85, -1),
              radius: 1.3,
              colors: [AppTokens.brand500.withValues(alpha: 0.18), fade],
              stops: const [0, 0.62],
            ),
          ),
        ),
        DecoratedBox(
          decoration: BoxDecoration(
            gradient: RadialGradient(
              center: const Alignment(1, -0.9),
              radius: 1.1,
              colors: [AppTokens.accent500.withValues(alpha: 0.13), fade],
              stops: const [0, 0.58],
            ),
          ),
        ),
        DecoratedBox(
          decoration: BoxDecoration(
            gradient: RadialGradient(
              center: const Alignment(0.1, 1.15),
              radius: 1,
              colors: [AppTokens.info500.withValues(alpha: 0.10), fade],
              stops: const [0, 0.6],
            ),
          ),
        ),
        // 极淡网格（28pt 栅格），给品牌光晕一层秩序感
        CustomPaint(painter: _GridPainter(color: t.borderSubtle)),
        child,
      ],
    );
  }
}

/// 背景网格线（只画线，不填充，保证内容区域干净）
class _GridPainter extends CustomPainter {
  const _GridPainter({required this.color});

  final Color color;

  /// 网格步长（28pt）
  static const double step = 28;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = 1;
    for (var x = 0.0; x <= size.width; x += step) {
      canvas.drawLine(Offset(x, 0), Offset(x, size.height), paint);
    }
    for (var y = 0.0; y <= size.height; y += step) {
      canvas.drawLine(Offset(0, y), Offset(size.width, y), paint);
    }
  }

  @override
  bool shouldRepaint(_GridPainter oldDelegate) => oldDelegate.color != color;
}
