import '../utils/http_service.dart';

/// 配置查询服务
/// 对标 jflove-desktop/src/services/config_service.py
class ConfigService {
  final HttpService _http;

  ConfigService(this._http);

  /// 查询服务端配置
  /// 对应服务端：GET /api/v1/config（需 admin 权限）
  Future<Map<String, dynamic>> getConfig() async {
    return _http.encryptedGet('/api/v1/config', {});
  }
}
