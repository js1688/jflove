import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../utils/logger.dart';

/// 三态主题模式
///
/// 与桌面端 `ThemeManager` 的合法模式一一对应，便于三端行为对齐：
///   - [system]：跟随系统（默认）
///   - [light] ：强制亮色
///   - [dark]  ：强制暗色
enum AppThemeMode {
  system('system', '跟随系统'),
  light('light', '亮色'),
  dark('dark', '暗色');

  const AppThemeMode(this.key, this.label);

  /// 持久化用的稳定标识（不要用 enum.index，重排序会串档）
  final String key;

  /// 界面展示名
  final String label;

  /// 由持久化标识反解，未知值一律回退到 [system]
  static AppThemeMode fromKey(String? key) {
    for (final mode in AppThemeMode.values) {
      if (mode.key == key) return mode;
    }
    return AppThemeMode.system;
  }
}

/// 主题模式状态
///
/// 说明：主题偏好**不是敏感数据**，但移动端目前只有 `flutter_secure_storage`
/// 这一个已接入的持久化通道（`SessionManager` 复用），为了不引入新依赖与
/// 新的存储原语，这里沿用同一通道；写入的是模式字符串，不涉及任何密钥内容。
class ThemeModeNotifier extends Notifier<AppThemeMode> {
  /// 安全存储内的字段名（与 Session 的 JSON 单键不同，独立成键）
  static const String storageKey = 'theme_mode';

  final FlutterSecureStorage _storage = const FlutterSecureStorage();

  @override
  AppThemeMode build() => AppThemeMode.system;

  /// 从持久化存储恢复；读取失败时静默保持默认值
  ///
  /// 该方法在 `main()` 中、`runApp` 之前调用，保证首帧就是正确主题，
  /// 不会出现"先亮后暗"的闪烁。
  Future<void> load() async {
    try {
      final raw = await _storage.read(key: storageKey);
      state = AppThemeMode.fromKey(raw);
    } catch (e) {
      // 存储不可用（首次启动、密钥库异常）不是错误，用默认值即可
      log.warning('主题偏好读取失败，回退跟随系统: $e');
      state = AppThemeMode.system;
    }
  }

  /// 切换主题并持久化
  Future<void> setMode(AppThemeMode mode) async {
    if (state == mode) return;
    state = mode;
    try {
      await _storage.write(key: storageKey, value: mode.key);
    } catch (e) {
      // 写失败不影响本次会话的显示效果，仅记录
      log.warning('主题偏好写入失败: $e');
    }
  }

  /// 在 跟随系统 → 亮色 → 暗色 之间循环（供快捷按钮使用）
  Future<void> cycle() async {
    final index = AppThemeMode.values.indexOf(state);
    final next = AppThemeMode.values[(index + 1) % AppThemeMode.values.length];
    await setMode(next);
  }
}

/// 主题模式 Provider
final themeModeProvider =
    NotifierProvider<ThemeModeNotifier, AppThemeMode>(ThemeModeNotifier.new);

/// 把 [AppThemeMode] 映射为 Flutter 的 [ThemeMode]
ThemeMode toMaterialThemeMode(AppThemeMode mode) {
  switch (mode) {
    case AppThemeMode.system:
      return ThemeMode.system;
    case AppThemeMode.light:
      return ThemeMode.light;
    case AppThemeMode.dark:
      return ThemeMode.dark;
  }
}
