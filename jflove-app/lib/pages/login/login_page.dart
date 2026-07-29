import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../providers/session_provider.dart';
import '../../services/server_history_service.dart';

/// 登录页
///
/// 对标桌面端 login_window.py。
/// 功能：服务器地址 + 历史记录 + 密钥交换 + 管理员初始化 + 用户登录 + TTL 选择。
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
    return Scaffold(
      appBar: AppBar(title: const Text('JFLove')),
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 400),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(
                  Icons.lock_outline,
                  size: 64,
                  color: Theme.of(context).colorScheme.primary,
                ),
                const SizedBox(height: 8),
                Text(
                  'JFLove',
                  style: Theme.of(context).textTheme.headlineMedium,
                ),
                Text(
                  '私有文档 & 笔记管理',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
                const SizedBox(height: 32),

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
                      (context, textEditingController, focusNode, onSubmitted) {
                        // 同步 Autocomplete 的 controller 到 _serverController
                        _serverController.text = textEditingController.text;
                        return TextField(
                          controller: textEditingController,
                          focusNode: focusNode,
                          decoration: const InputDecoration(
                            labelText: '服务器地址',
                            border: OutlineInputBorder(),
                            hintText: 'http://192.168.1.100:8989',
                            prefixIcon: Icon(Icons.dns),
                          ),
                          onSubmitted: (_) => onSubmitted(),
                          onChanged: (v) => _serverController.text = v,
                        );
                      },
                ),
                const SizedBox(height: 12),

                // 用户名
                TextField(
                  controller: _usernameController,
                  decoration: const InputDecoration(
                    labelText: '用户名',
                    border: OutlineInputBorder(),
                    prefixIcon: Icon(Icons.person),
                  ),
                ),
                const SizedBox(height: 12),

                // 密码
                TextField(
                  controller: _passwordController,
                  obscureText: true,
                  decoration: const InputDecoration(
                    labelText: '密码',
                    border: OutlineInputBorder(),
                    prefixIcon: Icon(Icons.lock),
                  ),
                ),
                const SizedBox(height: 12),

                // Token 有效期
                DropdownButtonFormField<int>(
                  initialValue: _selectedTtl,
                  decoration: const InputDecoration(
                    labelText: '登录有效期',
                    border: OutlineInputBorder(),
                    prefixIcon: Icon(Icons.timer),
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

                // 错误提示
                if (_errorMsg != null) ...[
                  const SizedBox(height: 12),
                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: Theme.of(context).colorScheme.errorContainer,
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Row(
                      children: [
                        Icon(
                          Icons.error_outline,
                          color: Theme.of(context).colorScheme.error,
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(
                            _errorMsg!,
                            style: TextStyle(
                              color: Theme.of(context).colorScheme.error,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],

                const SizedBox(height: 24),

                // 登录按钮
                SizedBox(
                  width: double.infinity,
                  height: 48,
                  child: FilledButton(
                    onPressed: _isLoading ? null : _handleLogin,
                    child: _isLoading
                        ? const SizedBox(
                            width: 20,
                            height: 20,
                            child: CircularProgressIndicator(
                              strokeWidth: 2,
                              color: Colors.white,
                            ),
                          )
                        : const Text('登录', style: TextStyle(fontSize: 16)),
                  ),
                ),
              ],
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
