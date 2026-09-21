import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart' show rootBundle;
import 'package:path_provider/path_provider.dart';

import '../../config/mermaid_theme.dart';
import 'mermaid_types.dart';

/// Mermaid 渲染服务（移动端）
///
/// 方案（设计文档 §4.4）：把自包含的 mermaid IIFE 内联进一个本地 HTML 模板，
/// 用 `webview_flutter` 的 `loadHtmlString` 加载渲染，结果通过 JavaScript
/// 通道回传。**全程零网络依赖**（页面不引用任何远程资源）。
///
/// 缓存：结果按 `sha256(code|theme|version)[:16]` 落磁盘（应用缓存目录），
/// 命中时不创建 WebView，二次打开秒开。
class MermaidRenderService {
  MermaidRenderService._();

  static final MermaidRenderService instance = MermaidRenderService._();

  /// 与三端锁定的 mermaid 版本一致（scripts/build_mermaid_bundle.mjs）
  static const String mermaidVersion = '11.16.0';

  static const String _templateAsset = 'assets/mermaid/renderer.html';

  /// 单图渲染超时
  static const Duration renderTimeout = Duration(seconds: 8);

  String? _cachedBundle;
  String? _cachedTemplate;

  int _seq = 0;

  /// 渲染页是否可用（模板与 bundle 都能读到）
  Future<bool> isAvailable() async {
    try {
      await _loadTemplate();
      await _loadBundle();
      return true;
    } catch (e) {
      debugPrint('[mermaid] 渲染资源不可用：$e');
      return false;
    }
  }

  /// 生成一次渲染所需的 HTML（已内联 bundle 与本次的 code/theme/宽度）。
  ///
  /// 返回值可直接交给 `WebViewController.loadHtmlString`。
  ///
  /// [width] 是图表**实际展示**的宽度（逻辑像素）。必须传：
  /// mermaid 按"能看到的宽度"排版，而 Android WebView 在没有 viewport meta 时
  /// 使用 **980px 的布局视口** —— 甘特图会按 980 排版，到 360 宽的屏幕上
  /// 要么被压到 ~0.37x（11px 字号变 4px，不可读）、要么只能看到一截，
  /// 用户反馈的「甘特图无法显示」就是这个。渲染页会把 html/body 钉成该宽度，
  /// 于是 mermaid 按真实展示宽度排版。
  Future<String> buildHtml({
    required String code,
    required MermaidTheme theme,
    required int width,
  }) async {
    final tpl = await _loadTemplate();
    final bundle = await _loadBundle();
    final id = _nextId();

    // 替换顺序有讲究：bundle 必须**第一个**替换。它对页面正文最长，
    // 先塞进去后续几次替换才不会在它的内容上做无谓扫描；反过来（先替换 code）
    // 一旦 bundle 文本里恰好出现占位符字样，就会被误替换。
    //
    // 占位符替换为**可直接使用的 JS 字面量**（字符串自带引号、主题是对象、宽度是数字），
    // 页面侧不做任何解析 —— 桌面端实测过 JSON.parse 链路在 WebEngine 里不可靠。
    final html = tpl
        .replaceAll('__JF_BUNDLE__', bundle)
        .replaceAll('__JF_CODE__', _jsString(code))
        .replaceAll('__JF_THEME__', _jsObject(theme.toPayload()))
        .replaceAll('__JF_ID__', _jsString(id))
        .replaceAll('__JF_WIDTH__', _jsNumber(width));

    // 自检 1：替换后不得残留占位符
    for (final ph in const [
      '__JF_CODE__',
      '__JF_THEME__',
      '__JF_ID__',
      '__JF_WIDTH__',
    ]) {
      if (html.contains(ph)) {
        throw StateError('渲染模板占位符 $ph 未被替换');
      }
    }

    // 自检 2：bundle 必须**恰好内联一次**。
    // 这条是被真实缺陷逼出来的：模板的 HTML 注释里曾经原样列出占位符名字，
    // 而替换是纯文本 replaceAll —— 注释里那份也被替换，3.4MB 的 bundle 于是
    // 在注释里又内联了一遍，每次渲染的页面从 3.4MB 变成 6.8MB。
    // 单次替换不可能产生两份，所以只要数出 >1 就说明模板里多写了一处。
    final firstAt = html.indexOf(bundle);
    if (firstAt >= 0 && html.indexOf(bundle, firstAt + bundle.length) >= 0) {
      throw StateError(
        '渲染模板里出现了多处 bundle 占位符（被内联了不止一次），'
        '请检查 assets/mermaid/renderer.html 的注释里是否原样写了占位符名字',
      );
    }

    return html;
  }

  /// 把渲染结果解析成 [MermaidResult]
  MermaidResult parseResult(String raw) {
    try {
      final map = jsonDecode(raw) as Map<String, dynamic>;
      if (map['ok'] == true) {
        return MermaidResult.success(
          svg: (map['svg'] ?? '') as String,
          width: (map['width'] as num?)?.toInt() ?? 0,
          height: (map['height'] as num?)?.toInt() ?? 0,
        );
      }
      final err = (map['error'] as Map?)?.cast<String, dynamic>() ?? const {};
      return MermaidResult.failure(
        reason: MermaidFailure.parse,
        message: (err['reason'] ?? '图表语法错误').toString(),
        line: (err['line'] as num?)?.toInt() ?? 0,
      );
    } catch (e) {
      return MermaidResult.failure(
        reason: MermaidFailure.runtime,
        message: '渲染结果无法解析',
      );
    }
  }

  // ── 磁盘缓存 ──────────────────────────────────────

  Future<MermaidResult?> readCache(String key) async {
    try {
      final f = await _cacheFile(key);
      if (!await f.exists()) return null;
      final data = jsonDecode(await f.readAsString()) as Map<String, dynamic>;
      final svg = data['svg'] as String?;
      if (svg == null || svg.isEmpty) return null;
      return MermaidResult.success(
        svg: svg,
        width: (data['width'] as num?)?.toInt() ?? 0,
        height: (data['height'] as num?)?.toInt() ?? 0,
        fromCache: true,
      );
    } catch (_) {
      return null;
    }
  }

  Future<void> writeCache(String key, MermaidResult result) async {
    if (!result.ok) return; // 失败结果不缓存，允许下次重试
    try {
      final f = await _cacheFile(key);
      await f.writeAsString(jsonEncode({
        'svg': result.svg,
        'width': result.width,
        'height': result.height,
        'version': mermaidVersion,
      }));
    } catch (e) {
      debugPrint('[mermaid] 写缓存失败：$e'); // 缓存失败不影响渲染
    }
  }

  /// 渲染页配置版本。
  ///
  /// **改动 `assets/mermaid/renderer.html` 时必须 +1**：渲染结果由
  /// 「源码 + 主题 + 渲染页配置」共同决定，缓存键里如果不体现配置版本，
  /// 改了模板却仍命中旧缓存，界面会毫无变化（桌面端实现期正是这样白排查了一轮）。
  /// Flutter 侧拿不到资源文件的 mtime，所以用一个手工维护的常量代替指纹。
  ///
  /// v3：渲染页新增尺寸归一化（修正 mermaid 写坏的 viewBox，修「甘特图无法预览」）。
  /// v4：尺寸归一化改为**按实测内容范围**计算，不再用 `svg.getBBox()` ——
  ///     后者会被 mermaid 故意画超长的辅助线污染（时序图生命线 y=2000、
  ///     甘特图今日线 x=28399），实测把甘特图 viewBox 写成 19289x128（压成一条线）、
  ///     时序图写成 440x2000（缩到面板顶部一小块）。
  ///     v3 缓存里存的是这些坏产物，所以必须 +1 让旧缓存失效。
  /// v5：渲染页按**真实展示宽度**排版（Dart 把宽度传进来，渲染页钉住 html/body）。
  ///     原因：Android WebView 默认 980px 布局视口，甘特图按 980 排版后到 360 宽屏上
  ///     不可读/看不全；实测钉成 360 后 viewBox 变成 `0 0 360 128`、字号 14px、整图可见。
  ///     宽度进了渲染结果 ⇒ 必须进缓存键，否则横竖屏切换会拿到另一方向尺寸的旧图。
  static const String renderConfigVersion = '5';

  /// 缓存键：图表源码 + 主题 + 引擎版本 + 渲染页配置版本 + **展示宽度**，任一变化即失效。
  ///
  /// 宽度必须进缓存键：mermaid 按宽度排版，同一份源码在手机竖屏与横屏下
  /// 产出的 SVG（viewBox / 换行）不同，不能互相命中。
  String cacheKey(String code, String themeName, int width) {
    final raw = '$code\n$themeName\n$mermaidVersion\n$renderConfigVersion\n$width';
    // 轻量稳定哈希（非加密用途，只做缓存键）
    var h1 = 0x811c9dc5;
    var h2 = 0x01000193;
    for (var i = 0; i < raw.length; i++) {
      final c = raw.codeUnitAt(i);
      h1 = ((h1 ^ c) * 0x01000193) & 0xFFFFFFFF;
      h2 = ((h2 ^ (c + i)) * 0x85ebca6b) & 0xFFFFFFFF;
    }
    return h1.toRadixString(16).padLeft(8, '0') +
        h2.toRadixString(16).padLeft(8, '0');
  }

  // ── 内部 ──────────────────────────────────────────

  Future<String> _loadTemplate() async {
    _cachedTemplate ??= await rootBundle.loadString(_templateAsset);
    return _cachedTemplate!;
  }

  Future<String> _loadBundle() async {
    if (_cachedBundle != null) return _cachedBundle!;
    // bundle 与模板同目录（assets/mermaid/）
    final raw = await rootBundle.loadString('assets/mermaid/mermaid.bundle.js');
    _cachedBundle = raw;
    return raw;
  }

  Future<File> _cacheFile(String key) async {
    final dir = await getApplicationCacheDirectory();
    final sub = Directory('${dir.path}/mermaid-cache');
    if (!await sub.exists()) {
      await sub.create(recursive: true);
    }
    return File('${sub.path}/$key.json');
  }

  String _nextId() {
    _seq += 1;
    return '$_seq-${DateTime.now().millisecondsSinceEpoch % 100000}';
  }

  /// JS 字符串字面量。`<` 转成 `\u003c` 防止内容里的 `</script>` 提前闭合标签。
  static String _jsString(String value) =>
      jsonEncode(value).replaceAll('<', r'\u003c');

  /// JS 对象字面量。`</` 拆成 `<\/`（对象字面量里是合法转义）。
  static String _jsObject(Object value) =>
      jsonEncode(value).replaceAll('</', r'<\/');

  /// JS 数字字面量。入参是 `int`，不可能出现 NaN/Infinity，直接落字面量即可。
  static String _jsNumber(int value) => value.toString();
}

/// 供测试访问的内部函数
@visibleForTesting
String jsStringForTest(String v) => MermaidRenderService._jsString(v);

@visibleForTesting
String jsObjectForTest(Object v) => MermaidRenderService._jsObject(v);
