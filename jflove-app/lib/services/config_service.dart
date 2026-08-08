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

  /// 更新服务端配置项
  /// 对应服务端：PUT /api/v1/config（需 admin 权限，写后立即生效、无需重启）
  Future<Map<String, dynamic>> updateConfig(String key, String value) async {
    return _http.encryptedPut('/api/v1/config', {'key': key, 'value': value});
  }
}
