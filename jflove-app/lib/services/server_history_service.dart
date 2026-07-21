import '../utils/session.dart';

/// 服务端连接历史服务
/// 对标 jflove-desktop/src/services/server_history_service.py
class ServerHistoryService {
  final SessionManager _session;

  ServerHistoryService(this._session);

  List<String> get history => _session.serverHistory;

  /// 添加服务器地址到历史
  Future<void> addServer(String url) async {
    _session.serverHistory.remove(url);
    _session.serverHistory.insert(0, url);
    if (_session.serverHistory.length > 10) {
      _session.serverHistory = _session.serverHistory.sublist(0, 10);
    }
    await _session.saveToStorage();
  }
}
