import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../config/design_tokens.dart';
import '../../providers/remote_provider.dart';
import '../../services/remote_ssh_service.dart';

/// 新建 / 编辑远程连接（v1.10.0）
///
/// 凭据语义与桌面端一致：编辑时输入框留空 = 不修改。
class RemoteEditPage extends ConsumerStatefulWidget {
  const RemoteEditPage({super.key, this.connectionId});

  /// 为 null 表示新建
  final int? connectionId;

  @override
  ConsumerState<RemoteEditPage> createState() => _RemoteEditPageState();
}

class _RemoteEditPageState extends ConsumerState<RemoteEditPage> {
  final _name = TextEditingController();
  final _host = TextEditingController();
  final _port = TextEditingController(text: '22');
  final _username = TextEditingController();
  final _password = TextEditingController();
  final _privateKey = TextEditingController();
  final _passphrase = TextEditingController();
  final _initCommand = TextEditingController();

  String _authType = 'password';
  String _mode = 'ssh';
  int _groupId = 0;
  int _jumpId = 0;
  bool _loading = true;
  bool _saving = false;
  bool _hasPassword = false;
  bool _hasPrivateKey = false;
  String _error = '';

  List<RemoteGroup> _groups = const [];
  List<RemoteConnection> _connections = const [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final api = ref.read(remoteApiProvider);
    try {
      final groups = await api.listGroups();
      final connections = await api.listConnections();
      if (widget.connectionId != null) {
        final detail = await api.getConnection(widget.connectionId!);
        _name.text = detail.name;
        _host.text = detail.host;
        _port.text = '${detail.port}';
        _username.text = detail.username;
        _authType = detail.authType;
        _mode = detail.mode;
        _groupId = detail.groupId;
        _jumpId = detail.jumpConnectionId;
        _initCommand.text = detail.initCommand;
        _hasPassword = detail.hasPassword;
        _hasPrivateKey = detail.hasPrivateKey;
      }
      if (!mounted) return;
      setState(() {
        _groups = groups;
        _connections = connections;
        _loading = false;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = '$error';
        _loading = false;
      });
    }
  }

  @override
  void dispose() {
    _name.dispose();
    _host.dispose();
    _port.dispose();
    _username.dispose();
    _password.dispose();
    _privateKey.dispose();
    _passphrase.dispose();
    _initCommand.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (_name.text.trim().isEmpty || _host.text.trim().isEmpty) {
      setState(() => _error = '名称和主机不能为空');
      return;
    }
    if (widget.connectionId == null &&
        _authType != 'publickey' &&
        _password.text.isEmpty) {
      setState(() => _error = '该认证方式需要密码');
      return;
    }
    if (widget.connectionId == null &&
        _authType == 'publickey' &&
        _privateKey.text.isEmpty) {
      setState(() => _error = '该认证方式需要私钥');
      return;
    }
    if (_initCommand.text.contains('\n') || _initCommand.text.contains('\r')) {
      setState(() => _error = '连接后执行命令只能是单行');
      return;
    }
    setState(() {
      _saving = true;
      _error = '';
    });
    final payload = <String, dynamic>{
      'name': _name.text.trim(),
      'host': _host.text.trim(),
      'port': int.tryParse(_port.text) ?? 22,
      'username': _username.text,
      'auth_type': _authType,
      'mode': _mode,
      'ssh_group_id': _groupId,
      'jump_connection_id': _jumpId,
      'init_command': _initCommand.text,
    };
    if (_password.text.isNotEmpty) payload['password'] = _password.text;
    if (_privateKey.text.isNotEmpty) {
      payload['private_key'] = _privateKey.text;
      payload['passphrase'] = _passphrase.text;
    }
    final api = ref.read(remoteApiProvider);
    try {
      if (widget.connectionId != null) {
        payload['id'] = widget.connectionId;
        await api.updateConnection(payload);
      } else {
        await api.createConnection(payload);
      }
      ref.invalidate(remoteOverviewProvider);
      if (!mounted) return;
      Navigator.of(context).pop();
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _saving = false;
        _error = '$error';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.connectionId == null ? '新建连接' : '编辑连接'),
        actions: [
          TextButton(
            onPressed: _saving ? null : _save,
            child: Text(_saving ? '保存中…' : '保存'),
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: EdgeInsets.all(t.s4),
              children: [
                TextField(controller: _name, decoration: const InputDecoration(labelText: '名称')),
                SizedBox(height: t.s3),
                Row(
                  children: [
                    Expanded(
                      flex: 3,
                      child: TextField(
                        controller: _host,
                        decoration: const InputDecoration(labelText: '主机'),
                      ),
                    ),
                    SizedBox(width: t.s3),
                    Expanded(
                      child: TextField(
                        controller: _port,
                        keyboardType: TextInputType.number,
                        decoration: const InputDecoration(labelText: '端口'),
                      ),
                    ),
                  ],
                ),
                SizedBox(height: t.s3),
                TextField(
                  controller: _username,
                  decoration: const InputDecoration(labelText: '用户名'),
                ),
                SizedBox(height: t.s3),
                SegmentedButton<String>(
                  segments: const [
                    ButtonSegment(value: 'ssh', label: Text('SSH 终端')),
                    ButtonSegment(value: 'sftp', label: Text('仅 SFTP')),
                  ],
                  selected: {_mode},
                  onSelectionChanged: (value) => setState(() => _mode = value.first),
                ),
                SizedBox(height: t.s3),
                SegmentedButton<String>(
                  segments: const [
                    ButtonSegment(value: 'password', label: Text('密码')),
                    ButtonSegment(value: 'publickey', label: Text('密钥')),
                    ButtonSegment(value: 'password_keyboard', label: Text('密码+OTP')),
                  ],
                  selected: {_authType},
                  onSelectionChanged: (value) => setState(() => _authType = value.first),
                ),
                SizedBox(height: t.s3),
                if (_authType != 'publickey')
                  TextField(
                    controller: _password,
                    obscureText: true,
                    decoration: InputDecoration(
                      labelText: '密码',
                      hintText: _hasPassword ? '已设置，留空不修改' : '输入密码',
                    ),
                  ),
                if (_authType == 'publickey') ...[
                  TextField(
                    controller: _privateKey,
                    maxLines: 6,
                    decoration: InputDecoration(
                      labelText: '私钥内容',
                      hintText: _hasPrivateKey ? '已设置，留空不修改' : '-----BEGIN OPENSSH PRIVATE KEY-----',
                    ),
                  ),
                  SizedBox(height: t.s3),
                  TextField(
                    controller: _passphrase,
                    obscureText: true,
                    decoration: InputDecoration(
                      labelText: '私钥口令（可选）',
                      hintText: _hasPrivateKey ? '已设置，留空不修改' : '未设置',
                    ),
                  ),
                ],
                SizedBox(height: t.s3),
                DropdownButtonFormField<int>(
                  initialValue: _jumpId,
                  decoration: const InputDecoration(labelText: '跳板连接'),
                  items: [
                    const DropdownMenuItem(value: 0, child: Text('直连')),
                    for (final item in _connections)
                      if (item.id != widget.connectionId)
                        DropdownMenuItem(value: item.id, child: Text(item.name)),
                  ],
                  onChanged: (value) => setState(() => _jumpId = value ?? 0),
                ),
                SizedBox(height: t.s3),
                DropdownButtonFormField<int>(
                  initialValue: _groupId,
                  decoration: const InputDecoration(labelText: '分组'),
                  items: [
                    const DropdownMenuItem(value: 0, child: Text('未分组')),
                    for (final group in _groups)
                      DropdownMenuItem(value: group.id, child: Text(group.name)),
                  ],
                  onChanged: (value) => setState(() => _groupId = value ?? 0),
                ),
                SizedBox(height: t.s3),
                TextField(
                  controller: _initCommand,
                  decoration: const InputDecoration(
                    labelText: '连接后执行（可选）',
                    hintText: 'cd /opt/app && ls',
                  ),
                ),
                if (_error.isNotEmpty) ...[
                  SizedBox(height: t.s3),
                  Text(_error, style: TextStyle(color: Theme.of(context).colorScheme.error)),
                ],
              ],
            ),
    );
  }
}
