import 'dart:convert';
import 'dart:io';

import 'package:dartssh2/dartssh2.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:path_provider/path_provider.dart';
import 'package:xterm/xterm.dart';

import '../../config/design_tokens.dart';
import '../../providers/remote_provider.dart';
import '../../services/remote_ssh_service.dart';

/// 远程终端 + SFTP（v1.10.0）
///
/// 一个连接 = 一页；页内用「终端 / 文件」切换，共用同一条 SSH 连接
/// （含跳板链，不重复认证）。
class RemoteTerminalPage extends ConsumerStatefulWidget {
  const RemoteTerminalPage({super.key, required this.connection});

  final RemoteConnection connection;

  @override
  ConsumerState<RemoteTerminalPage> createState() => _RemoteTerminalPageState();
}

class _RemoteTerminalPageState extends ConsumerState<RemoteTerminalPage> {
  RemoteSshClient? _session;
  Terminal? _terminal;
  String _status = 'connecting';
  String _error = '';
  int _tab = 0;
  bool get _isSftpOnly => widget.connection.mode == 'sftp';

  final _sftpPath = ValueNotifier<String>('.');
  final _entries = ValueNotifier<List<SftpName>>([]);
  bool _sftpLoading = false;

  @override
  void initState() {
    super.initState();
    // 首帧后再建连：主机指纹确认需要 showDialog，必须等 Navigator 就绪
    WidgetsBinding.instance.addPostFrameCallback((_) => _connect());
  }

  @override
  void dispose() {
    _session?.close();
    _sftpPath.dispose();
    _entries.dispose();
    super.dispose();
  }

  Future<bool> _askHostKey(String host, String fingerprint) async {
    final result = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('确认主机指纹'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('首次连接 $host，请核对指纹：'),
            const SizedBox(height: 8),
            SelectableText(
              fingerprint,
              style: const TextStyle(fontFamily: 'monospace', fontSize: 11),
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('拒绝')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('接受')),
        ],
      ),
    );
    return result ?? false;
  }

  Future<void> _connect() async {
    final api = ref.read(remoteApiProvider);
    try {
      final session = await RemoteSshClient.connect(
        widget.connection,
        api: api,
        onHostKey: _askHostKey,
      );
      final shell = session.shell;
      Terminal? terminal;
      if (shell != null) {
        terminal = Terminal(maxLines: 5000);
        terminal.onOutput = (data) => shell.write(utf8.encode(data));
        terminal.onResize =
            (width, height, pixelWidth, pixelHeight) => shell.resizeTerminal(width, height);
        shell.stdout.listen((data) {
          terminal!.write(utf8.decode(data, allowMalformed: true));
        });
        shell.stderr.listen((data) {
          terminal!.write(utf8.decode(data, allowMalformed: true));
        });
        shell.done.then((_) {
          if (mounted) setState(() => _status = 'closed');
        });
      }
      await api.touch(widget.connection.id);
      if (!mounted) return;
      setState(() {
        _session = session;
        _terminal = terminal;
        _status = 'ready';
        if (_isSftpOnly) {
          _tab = 1;
          _loadSftp('.');
        }
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _status = 'failed';
        _error = '$error';
      });
    }
  }

  Future<void> _loadSftp(String path) async {
    final session = _session;
    if (session == null) return;
    setState(() => _sftpLoading = true);
    try {
      final list = await session.sftp.listdir(path);
      _entries.value = list;
      _sftpPath.value = path;
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('读取目录失败：$error')),
      );
    } finally {
      if (mounted) setState(() => _sftpLoading = false);
    }
  }

  Future<void> _upload() async {
    final session = _session;
    if (session == null) return;
    final picked = await FilePicker.platform.pickFiles();
    final localPath = picked?.files.single.path;
    if (localPath == null) return;
    final name = picked!.files.single.name;
    try {
      final remote = _join(_sftpPath.value, name);
      final file = await session.sftp.open(
        remote,
        mode: SftpFileOpenMode.create | SftpFileOpenMode.write | SftpFileOpenMode.truncate,
      );
      // 分块写入，避免大文件整包读入内存
      final reader = await File(localPath).open();
      var offset = 0;
      try {
        while (true) {
          final chunk = await reader.read(256 * 1024);
          if (chunk.isEmpty) break;
          await file.writeBytes(chunk, offset: offset);
          offset += chunk.length;
        }
      } finally {
        await reader.close();
      }
      await file.close();
      await _loadSftp(_sftpPath.value);
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('上传失败：$error')),
      );
    }
  }

  Future<void> _download(SftpName entry) async {
    final session = _session;
    if (session == null) return;
    try {
      final dir = await getApplicationDocumentsDirectory();
      final target = File('${dir.path}/${entry.filename}');
      final remotePath = _join(_sftpPath.value, entry.filename);
      final file = await session.sftp.open(remotePath);
      final sink = target.openWrite();
      await for (final chunk in file.read()) {
        sink.add(chunk);
      }
      await sink.close();
      await file.close();
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('已下载到 ${target.path}')),
      );
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('下载失败：$error')),
      );
    }
  }

  Future<void> _delete(SftpName entry) async {
    final session = _session;
    if (session == null) return;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('删除远端文件'),
        content: Text('确定删除「${entry.filename}」？'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('删除')),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      final path = _join(_sftpPath.value, entry.filename);
      if (entry.attr.isDirectory) {
        await session.sftp.rmdir(path);
      } else {
        await session.sftp.remove(path);
      }
      await _loadSftp(_sftpPath.value);
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('删除失败：$error')),
      );
    }
  }

  String _join(String base, String name) {
    if (base == '.' || base.isEmpty) return name;
    return base.endsWith('/') ? '$base$name' : '$base/$name';
  }

  String _parent(String path) {
    if (path == '.' || path == '/') return '.';
    final parts = path.split('/').where((part) => part.isNotEmpty).toList();
    if (parts.length <= 1) return '.';
    parts.removeLast();
    return parts.join('/');
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    return Scaffold(
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(widget.connection.name, style: const TextStyle(fontSize: 16)),
            Text(
              '${widget.connection.username}@${widget.connection.host}',
              style: TextStyle(fontSize: 11, color: t.fgMuted, fontFamily: 'monospace'),
            ),
          ],
        ),
        actions: [
          if (!_isSftpOnly)
            IconButton(
              icon: const Icon(Icons.terminal_rounded),
              tooltip: '终端',
              onPressed: () => setState(() => _tab = 0),
            ),
          IconButton(
            icon: const Icon(Icons.folder_outlined),
            tooltip: '文件',
            onPressed: () {
              setState(() => _tab = 1);
              if (_entries.value.isEmpty) _loadSftp(_sftpPath.value);
            },
          ),
        ],
      ),
      body: _status == 'failed'
          ? Center(
              child: Padding(
                padding: EdgeInsets.all(t.s6),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.error_outline, color: Theme.of(context).colorScheme.error),
                    SizedBox(height: t.s2),
                    Text('连接失败', style: const TextStyle(fontWeight: FontWeight.w600)),
                    SizedBox(height: t.s1),
                    Text(_error, textAlign: TextAlign.center, style: TextStyle(color: t.fgMuted)),
                    SizedBox(height: t.s3),
                    FilledButton(onPressed: _connect, child: const Text('重试')),
                  ],
                ),
              ),
            )
          : _tab == 0
              ? _buildTerminal()
              : _buildSftp(),
      bottomNavigationBar: !_isSftpOnly && _tab == 0 && _status == 'ready' ? _buildKeys() : null,
    );
  }

  Widget _buildTerminal() {
    if (_status == 'connecting') {
      return const Center(child: CircularProgressIndicator());
    }
    final terminal = _terminal;
    if (terminal == null) {
      return const Center(child: Text('终端尚未就绪'));
    }
    return TerminalView(
      terminal,
      autofocus: true,
      backgroundOpacity: 1,
      textStyle: const TerminalStyle(fontSize: 13, fontFamily: 'JetBrains Mono'),
    );
  }

  Widget _buildSftp() {
    final t = context.tokens;
    return Column(
      children: [
        Container(
          padding: EdgeInsets.symmetric(horizontal: t.s2, vertical: t.s1),
          color: t.bgSurface,
          child: Row(
            children: [
              IconButton(
                icon: const Icon(Icons.arrow_upward_rounded),
                onPressed: () => _loadSftp(_parent(_sftpPath.value)),
              ),
              Expanded(
                child: Text(
                  _sftpPath.value,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
                ),
              ),
              IconButton(
                icon: const Icon(Icons.refresh_rounded),
                onPressed: () => _loadSftp(_sftpPath.value),
              ),
              IconButton(
                icon: const Icon(Icons.upload_file_rounded),
                tooltip: '上传',
                onPressed: _upload,
              ),
            ],
          ),
        ),
        if (_sftpLoading) const LinearProgressIndicator(minHeight: 2),
        Expanded(
          child: ValueListenableBuilder<List<SftpName>>(
            valueListenable: _entries,
            builder: (context, entries, _) {
              if (entries.isEmpty) {
                return const Center(child: Text('空目录'));
              }
              return ListView.builder(
                itemCount: entries.length,
                itemBuilder: (context, index) {
                  final entry = entries[index];
                  final isDir = entry.attr.isDirectory;
                  return ListTile(
                    dense: true,
                    leading: Icon(
                      isDir ? Icons.folder_rounded : Icons.insert_drive_file_outlined,
                      color: isDir ? AppTokens.brand500 : t.fgMuted,
                    ),
                    title: Text(entry.filename, maxLines: 1, overflow: TextOverflow.ellipsis),
                    subtitle: Text(
                      isDir ? '目录' : _formatSize(entry.attr.size ?? 0),
                      style: TextStyle(fontSize: 11, color: t.fgSubtle),
                    ),
                    onTap: () {
                      if (isDir) _loadSftp(_join(_sftpPath.value, entry.filename));
                    },
                    onLongPress: () => showModalBottomSheet<void>(
                      context: context,
                      builder: (ctx) => SafeArea(
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            if (!isDir)
                              ListTile(
                                leading: const Icon(Icons.download_rounded),
                                title: const Text('下载'),
                                onTap: () {
                                  Navigator.pop(ctx);
                                  _download(entry);
                                },
                              ),
                            ListTile(
                              leading: const Icon(Icons.delete_outline),
                              title: const Text('删除'),
                              onTap: () {
                                Navigator.pop(ctx);
                                _delete(entry);
                              },
                            ),
                          ],
                        ),
                      ),
                    ),
                  );
                },
              );
            },
          ),
        ),
      ],
    );
  }

  Widget _buildKeys() {
    final session = _session;
    if (session == null) return const SizedBox.shrink();
    void send(String data) => session.shell?.write(utf8.encode(data));
    final keys = <String, String>{
      'Ctrl': '\x03',
      'Tab': '\t',
      'Esc': '\x1b',
      '←': '\x1b[D',
      '↑': '\x1b[A',
      '↓': '\x1b[B',
      '→': '\x1b[C',
    };
    return SafeArea(
      child: Container(
        height: 46,
        color: const Color(0xFF0D1422),
        child: ListView(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: 8),
          children: [
            for (final entry in keys.entries)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 3, vertical: 7),
                child: TextButton(
                  style: TextButton.styleFrom(
                    backgroundColor: const Color(0xFF151F33),
                    foregroundColor: const Color(0xFF94A3B8),
                    padding: const EdgeInsets.symmetric(horizontal: 12),
                    minimumSize: const Size(0, 32),
                  ),
                  onPressed: () => send(entry.value),
                  child: Text(entry.key, style: const TextStyle(fontSize: 12)),
                ),
              ),
          ],
        ),
      ),
    );
  }

  String _formatSize(int size) {
    if (size < 1024) return '$size B';
    if (size < 1024 * 1024) return '${(size / 1024).toStringAsFixed(1)} KB';
    return '${(size / 1024 / 1024).toStringAsFixed(1)} MB';
  }
}
