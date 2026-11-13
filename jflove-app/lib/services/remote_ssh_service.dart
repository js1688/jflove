import 'dart:convert';

import 'package:dartssh2/dartssh2.dart';

import '../utils/http_service.dart';

/// 远程连接分组（v1.10.0）
class RemoteGroup {
  const RemoteGroup({
    required this.id,
    required this.name,
    required this.sortOrder,
    required this.connectionCount,
  });

  final int id;
  final String name;
  final int sortOrder;
  final int connectionCount;

  factory RemoteGroup.fromJson(Map<String, dynamic> json) => RemoteGroup(
        id: (json['id'] as num?)?.toInt() ?? 0,
        name: json['name'] as String? ?? '',
        sortOrder: (json['sort_order'] as num?)?.toInt() ?? 0,
        connectionCount: (json['connection_count'] as num?)?.toInt() ?? 0,
      );
}

/// 远程连接配置
///
/// 列表接口不返回凭据原文（只有 `hasPassword` / `hasPrivateKey`）；
/// 详情接口才带 `password` / `privateKey`，且只用于本机建连，不上报任何地方。
class RemoteConnection {
  const RemoteConnection({
    required this.id,
    required this.groupId,
    required this.name,
    required this.host,
    required this.port,
    required this.username,
    required this.authType,
    this.mode = 'ssh',
    required this.jumpConnectionId,
    required this.hasPassword,
    required this.hasPrivateKey,
    this.terminalType = 'xterm-256color',
    this.charset = 'UTF-8',
    this.initCommand = '',
    this.hostKeyFingerprint = '',
    this.lastConnectedAt,
    this.password,
    this.privateKey,
    this.passphrase,
  });

  final int id;
  final int groupId;
  final String name;
  final String host;
  final int port;
  final String username;
  final String authType;
  /// ssh = 终端 + 可选 SFTP；sftp = 纯 SFTP（不建立 shell）
  final String mode;
  final int jumpConnectionId;
  final bool hasPassword;
  final bool hasPrivateKey;
  final String terminalType;
  final String charset;
  final String initCommand;
  final String hostKeyFingerprint;
  final String? lastConnectedAt;
  final String? password;
  final String? privateKey;
  final String? passphrase;

  factory RemoteConnection.fromJson(Map<String, dynamic> json) => RemoteConnection(
        id: (json['id'] as num?)?.toInt() ?? 0,
        groupId: (json['ssh_group_id'] as num?)?.toInt() ?? 0,
        name: json['name'] as String? ?? '',
        host: json['host'] as String? ?? '',
        port: (json['port'] as num?)?.toInt() ?? 22,
        username: json['username'] as String? ?? '',
        authType: json['auth_type'] as String? ?? 'password',
        mode: json['mode'] as String? ?? 'ssh',
        jumpConnectionId: (json['jump_connection_id'] as num?)?.toInt() ?? 0,
        hasPassword: json['has_password'] as bool? ?? false,
        hasPrivateKey: json['has_private_key'] as bool? ?? false,
        terminalType: json['terminal_type'] as String? ?? 'xterm-256color',
        charset: json['charset'] as String? ?? 'UTF-8',
        initCommand: json['init_command'] as String? ?? '',
        hostKeyFingerprint: json['host_key_fingerprint'] as String? ?? '',
        lastConnectedAt: json['last_connected_at'] as String?,
        password: json['password'] as String?,
        privateKey: json['private_key'] as String?,
        passphrase: json['passphrase'] as String?,
      );
}

/// 与服务端交换连接配置（不建立任何 SSH 连接）
class RemoteApi {
  RemoteApi(this._http);

  final HttpService _http;

  Future<List<RemoteGroup>> listGroups() async {
    final res = await _http.encryptedPost('/api/v1/ssh/groups/list', {});
    final list = res['groups'] as List<dynamic>? ?? const [];
    return list.map((e) => RemoteGroup.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<int> createGroup(String name) async {
    final res = await _http.encryptedPost('/api/v1/ssh/groups/create', {'name': name});
    return (res['id'] as num?)?.toInt() ?? 0;
  }

  Future<void> deleteGroup(int id) async {
    await _http.encryptedPost('/api/v1/ssh/groups/delete', {'id': id});
  }

  Future<List<RemoteConnection>> listConnections({String keyword = ''}) async {
    final res = await _http.encryptedPost(
      '/api/v1/ssh/connections/list',
      {'keyword': keyword},
    );
    final list = res['connections'] as List<dynamic>? ?? const [];
    return list.map((e) => RemoteConnection.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<RemoteConnection> getConnection(int id) async {
    final res = await _http.encryptedPost('/api/v1/ssh/connections/get', {'id': id});
    return RemoteConnection.fromJson(res['connection'] as Map<String, dynamic>);
  }

  Future<int> createConnection(Map<String, dynamic> payload) async {
    final res = await _http.encryptedPost('/api/v1/ssh/connections/create', payload);
    return (res['id'] as num?)?.toInt() ?? 0;
  }

  Future<void> updateConnection(Map<String, dynamic> payload) async {
    await _http.encryptedPost('/api/v1/ssh/connections/update', payload);
  }

  Future<void> deleteConnection(int id) async {
    await _http.encryptedPost('/api/v1/ssh/connections/delete', {'id': id});
  }

  /// 主机指纹确认后回写（只写这一列，其它字段由服务端沿用当前值）
  Future<void> saveHostKey(int id, String fingerprint) async {
    await _http.encryptedPost(
      '/api/v1/ssh/connections/update',
      {'id': id, 'host_key_fingerprint': fingerprint},
    );
  }

  Future<void> touch(int id) async {
    try {
      await _http.encryptedPost('/api/v1/ssh/connections/touch', {'id': id});
    } catch (_) {
      // 最近连接时间只用于展示，失败不影响主流程
    }
  }
}

/// 一条已建立的 SSH 会话（含跳板链）
class RemoteSshClient {
  RemoteSshClient._(this._chain, this.shell, this.sftp);

  final List<SSHClient> _chain;
  final SSHSession? shell;
  final SftpClient sftp;

  static Future<RemoteSshClient> connect(
    RemoteConnection connection, {
    int cols = 80,
    int rows = 24,
    required RemoteApi api,
    required Future<bool> Function(String host, String fingerprint) onHostKey,
  }) async {
    final chain = <RemoteConnection>[];
    var current = connection;
    final seen = <int>{};
    while (true) {
      if (!seen.add(current.id)) {
        throw StateError('跳板链路存在循环');
      }
      chain.add(current);
      if (current.jumpConnectionId == 0) break;
      current = await api.getConnection(current.jumpConnectionId);
      if (chain.length > 16) throw StateError('跳板链路超过 16 级');
    }
    // chain 是「目标 → 跳板1 → 跳板2」，反转后才是建立顺序
    final ordered = chain.reversed.toList();

    SSHSocket socket = await SSHSocket.connect(
      ordered.first.host,
      ordered.first.port,
      timeout: const Duration(seconds: 15),
    );
    final clients = <SSHClient>[];
    for (var i = 0; i < ordered.length; i++) {
      final row = ordered[i];
      final client = SSHClient(
        socket,
        username: row.username,
        onPasswordRequest: () => row.password ?? '',
        identities: _identities(row),
        onVerifyHostKey: (type, fingerprint) async {
          final digest = 'SHA256:${base64Encode(fingerprint).replaceAll('=', '')}';
          if (row.hostKeyFingerprint.isNotEmpty) {
            return row.hostKeyFingerprint == digest;
          }
          final accepted = await onHostKey(row.host, digest);
          if (accepted) await api.saveHostKey(row.id, digest);
          return accepted;
        },
        keepAliveInterval: const Duration(seconds: 20),
        handshakeTimeout: const Duration(seconds: 15),
        authTimeout: const Duration(seconds: 20),
      );
      await client.authenticated;
      clients.add(client);
      if (i < ordered.length - 1) {
        final next = ordered[i + 1];
        socket = await client.forwardLocal(next.host, next.port);
      }
    }

    final target = clients.last;
    // 纯 SFTP 连接不建立 shell：部分服务器只开放 SFTP，没有 shell
    final SSHSession? shell = connection.mode == 'sftp'
        ? null
        : await target.shell(
            pty: SSHPtyConfig(type: connection.terminalType, width: cols, height: rows),
          );
    final sftp = await target.sftp();
    final initCommand = connection.initCommand.trim();
    if (shell != null && initCommand.isNotEmpty) {
      shell.write(utf8.encode('$initCommand\n'));
    }
    return RemoteSshClient._(clients, shell, sftp);
  }

  static List<SSHKeyPair>? _identities(RemoteConnection row) {
    final pem = row.privateKey;
    if (pem == null || pem.isEmpty) return null;
    try {
      return SSHKeyPair.fromPem(pem, row.passphrase);
    } catch (_) {
      return null;
    }
  }

  void close() {
    for (final client in _chain.reversed) {
      client.close();
    }
  }
}
