import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show Clipboard, ClipboardData;
import 'package:webview_flutter/webview_flutter.dart';

import '../../config/design_tokens.dart';
import '../../config/mermaid_theme.dart';
import '../../services/mermaid/mermaid_render_service.dart';
import '../../services/mermaid/mermaid_types.dart';

/// Mermaid 图块（移动端）
///
/// 能力对照需求 AC-8 ~ AC-14：
///   - 缓存命中直接显示（不创建 WebView）；未命中才渲染
///   - 渲染中固定高度骨架，成图后不跳动
///   - 全屏查看 / 复制源码 / 导出
///   - 语法错误只降级本图块（含行号），其余内容照常
///   - 引擎不可用降级为源码卡，不报错不白屏
///
/// 渲染用 `WebViewWidget` 承载本地渲染页：完全离线、零网络依赖。
class MermaidBlock extends StatefulWidget {
  const MermaidBlock({
    super.key,
    required this.code,
    required this.index,
    this.onJumpToSource,
  });

  /// mermaid 源码（属于笔记正文，不写入日志）
  final String code;

  /// 在文档中的序号
  final int index;

  /// 点击「跳到源码」的回调
  final void Function(int index)? onJumpToSource;

  @override
  State<MermaidBlock> createState() => _MermaidBlockState();
}

enum _Phase { loading, rendering, done, error }

class _MermaidBlockState extends State<MermaidBlock> {
  final _service = MermaidRenderService.instance;

  _Phase _phase = _Phase.loading;
  MermaidResult? _result;
  WebViewController? _controller;
  Timer? _timeout;
  bool _disposed = false;

  /// 上一次渲染所用的主题亮度（首帧为 null，用于区分"首次进入"与"主题变了"）
  Brightness? _lastBrightness;

  String get _kind => detectDiagramKind(widget.code);

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _start());
  }

  @override
  void didUpdateWidget(covariant MermaidBlock oldWidget) {
    super.didUpdateWidget(oldWidget);
    // 源码变化（编辑器实时预览）或序号变化时重渲染
    if (oldWidget.code != widget.code || oldWidget.index != widget.index) {
      _reset();
      _start();
    }
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    // 主题（亮/暗）变化必须重渲染：mermaid 的颜色是通过 themeVariables
    // 在渲染时**烘焙进 SVG** 的，不是 SVG 里的 CSS 变量。只靠 cacheKey 区分
    // 主题是不够的 —— 已经渲染好的图块不会自己重建。切到暗色后如果不重渲染，
    // 深色底上会留着一整块亮色图表。
    final brightness = Theme.of(context).brightness;
    if (_lastBrightness != null && _lastBrightness != brightness) {
      _lastBrightness = brightness;
      _reset();
      _start();
      return;
    }
    _lastBrightness = brightness;
  }

  @override
  void dispose() {
    _disposed = true;
    _timeout?.cancel();
    _controller = null; // 释放 WebView 引用
    super.dispose();
  }

  void _reset() {
    _timeout?.cancel();
    _controller = null;
    _result = null;
    _phase = _Phase.loading;
  }

  /// 图表**实际展示**的宽度（逻辑像素），交给渲染页用来排版。
  ///
  /// 为什么需要它：mermaid 按"能看到的宽度"排版，而 Android WebView 在没有
  /// viewport meta 时使用 **980px 布局视口** —— 甘特图会按 980 排版，到 360 宽的
  /// 屏幕上要么被压到 ~0.37x（11px 字号变 4px，不可读）、要么只能看到一截。
  /// 用户反馈的「甘特图无法显示」正是这个（其它图各自按内容排版，宽度天然合适，
  /// 所以只有甘特图中招）。
  ///
  /// 取块自身的实测宽度最准；拿不到时退回"屏幕宽 - 内边距"。
  int _targetRenderWidth() {
    var w = 0.0;
    try {
      w = context.size?.width ?? 0;
    } catch (_) {
      // 尚未布局完成时 context.size 不可用：退回屏幕宽度
      w = 0;
    }
    if (!(w > 0)) {
      w = MediaQuery.maybeSizeOf(context)?.width ?? 360;
      w -= 32; // 页面左右内边距 + 卡片边框的粗略扣除
    }
    return w.clamp(240, 1200).round();
  }

  Future<void> _start() async {
    final brightness = Theme.of(context).brightness;
    final theme = MermaidTheme.of(brightness);
    final width = _targetRenderWidth();
    final key = _service.cacheKey(widget.code, theme.name, width);

    // 1) 缓存命中：直接显示，不创建 WebView
    final cached = await _service.readCache(key);
    if (_disposed) return;
    if (cached != null && cached.ok) {
      setState(() {
        _result = cached;
        _phase = _Phase.done;
      });
      return;
    }

    // 2) 引擎可用性
    final available = await _service.isAvailable();
    if (_disposed) return;
    if (!available) {
      setState(() {
        _result = MermaidResult.failure(
          reason: MermaidFailure.engine,
          message: '渲染资源不可用',
        );
        _phase = _Phase.error;
      });
      return;
    }

    // 3) 用 WebView 渲染
    try {
      final html = await _service.buildHtml(
        code: widget.code,
        theme: theme,
        width: width,
      );
      if (_disposed) return;
      _renderWithWebView(html, key);
    } catch (e) {
      if (_disposed) return;
      setState(() {
        _result = MermaidResult.failure(
          reason: MermaidFailure.runtime,
          message: '渲染页构建失败',
        );
        _phase = _Phase.error;
      });
    }
  }

  void _renderWithWebView(String html, String cacheKey) {
    setState(() => _phase = _Phase.rendering);

    final controller = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setBackgroundColor(Colors.transparent)
      // 只允许本地内容：拦截一切非 about:blank / data: 的导航，
      // 保证渲染页永远不会回连网络（安全宪法 §9.4）。
      ..setNavigationDelegate(
        NavigationDelegate(
          onNavigationRequest: (request) {
            final url = request.url;
            if (url.startsWith('about:') || url.startsWith('data:')) {
              return NavigationDecision.navigate;
            }
            return NavigationDecision.prevent;
          },
        ),
      )
      ..addJavaScriptChannel(
        'JFLoveBridge',
        onMessageReceived: (message) => _onBridgeMessage(message.message, cacheKey),
      );

    _controller = controller;

    _timeout?.cancel();
    _timeout = Timer(MermaidRenderService.renderTimeout, () {
      if (_disposed || _phase == _Phase.done) return;
      setState(() {
        _result = MermaidResult.failure(
          reason: MermaidFailure.timeout,
          message: '渲染超过 ${MermaidRenderService.renderTimeout.inSeconds} 秒',
        );
        _phase = _Phase.error;
      });
    });

    controller.loadHtmlString(html);
  }

  void _onBridgeMessage(String raw, String cacheKey) {
    if (_disposed) return;
    _timeout?.cancel();
    final result = _service.parseResult(raw);
    if (result.ok) {
      unawaited(_service.writeCache(cacheKey, result));
    }
    setState(() {
      _result = result;
      _phase = result.ok ? _Phase.done : _Phase.error;
    });
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    switch (_phase) {
      case _Phase.loading:
      case _Phase.rendering:
        return _shell(
          child: _Skeleton(tokens: t),
          footer: Text('渲染图表中…', style: TextStyle(fontSize: 11, color: t.fgSubtle)),
        );
      case _Phase.error:
        return _errorCard(t);
      case _Phase.done:
        return _shell(
          child: _webView(t),
          footer: _footer(t),
        );
    }
  }

  Widget _webView(AppTokens t) {
    final controller = _controller;
    if (controller == null) {
      // 缓存命中路径：没有 WebView，直接把 SVG 交给一个只读渲染页
      return _svgFallback(t);
    }
    final height = _result?.height ?? 200;
    return SizedBox(
      height: height.clamp(120, 640).toDouble(),
      child: WebViewWidget(controller: controller),
    );
  }

  /// 缓存命中时用一个轻量渲染页显示 SVG（仍走渲染页，保持一致的样式与交互）
  Widget _svgFallback(AppTokens t) {
    final svg = _result?.svg ?? '';
    if (svg.isEmpty) return _Skeleton(tokens: t);
    return SizedBox(
      height: (_result?.height ?? 200).clamp(120, 640).toDouble(),
      child: _StaticSvgView(svg: svg),
    );
  }

  /// 图块外壳：头部（类型标签 + 操作）、画布、底部信息
  Widget _shell({required Widget child, required Widget footer}) {
    final t = context.tokens;
    return Container(
      margin: const EdgeInsets.symmetric(vertical: 10),
      decoration: BoxDecoration(
        color: t.bgSurface,
        borderRadius: BorderRadius.circular(t.rLg),
        border: Border.all(color: t.borderDefault),
        boxShadow: t.e2,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // 头部
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
            decoration: BoxDecoration(
              color: t.bgSunken,
              borderRadius: BorderRadius.vertical(top: Radius.circular(t.rLg - 1)),
              border: Border(bottom: BorderSide(color: t.borderSubtle)),
            ),
            child: Row(
              children: [
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                  decoration: BoxDecoration(
                    color: t.bgActive,
                    borderRadius: BorderRadius.circular(999),
                  ),
                  child: Text(
                    _kind.toUpperCase(),
                    style: TextStyle(
                      fontSize: 10,
                      fontWeight: FontWeight.w700,
                      letterSpacing: 0.4,
                      color: AppTokens.brand600,
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Text(
                  '图表 ${widget.index + 1}',
                  style: TextStyle(fontSize: 11, color: t.fgSubtle),
                ),
                const Spacer(),
                _iconBtn(Icons.copy_rounded, '复制源码', _copyCode, t),
                if (_phase == _Phase.done)
                  _iconBtn(Icons.fullscreen_rounded, '全屏查看', _openViewer, t),
              ],
            ),
          ),
          // 画布
          Padding(padding: const EdgeInsets.all(10), child: child),
          // 底部
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
            decoration: BoxDecoration(
              border: Border(top: BorderSide(color: t.borderSubtle)),
            ),
            child: footer,
          ),
        ],
      ),
    );
  }

  Widget _footer(AppTokens t) {
    final cached = _result?.fromCache ?? false;
    return Row(
      children: [
        Icon(Icons.check_circle_outline_rounded, size: 13, color: AppTokens.success500),
        const SizedBox(width: 4),
        Text(
          cached ? '来自缓存' : '语法校验通过',
          style: TextStyle(fontSize: 11, color: t.fgSubtle),
        ),
        const Spacer(),
        Text(
          '双指缩放查看细节',
          style: TextStyle(fontSize: 11, color: t.fgSubtle),
        ),
      ],
    );
  }

  /// 错误 / 引擎不可用：只降级本图块（AC-12 / AC-14）
  Widget _errorCard(AppTokens t) {
    final r = _result;
    final isParse = r?.reason == MermaidFailure.parse;
    final line = r?.line ?? 0;
    return Container(
      margin: const EdgeInsets.symmetric(vertical: 10),
      decoration: BoxDecoration(
        color: t.bgSurface,
        borderRadius: BorderRadius.circular(t.rLg),
        border: Border.all(color: AppTokens.danger500.withValues(alpha: 0.35)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
            decoration: BoxDecoration(
              color: AppTokens.danger500.withValues(alpha: 0.08),
              borderRadius: BorderRadius.vertical(top: Radius.circular(t.rLg - 1)),
            ),
            child: Row(
              children: [
                Icon(Icons.error_outline_rounded, size: 14, color: AppTokens.danger500),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    line > 0
                        ? '${r?.displayReason ?? '渲染失败'} · 第 $line 行：${r?.message ?? ''}'
                        : '${r?.displayReason ?? '渲染失败'}：${r?.message ?? ''}',
                    style: TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.w600,
                      color: AppTokens.danger500,
                    ),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                _iconBtn(Icons.copy_rounded, '复制源码', _copyCode, t),
                if (widget.onJumpToSource != null)
                  _iconBtn(Icons.edit_rounded, '跳到源码', () {
                    widget.onJumpToSource!(widget.index);
                  }, t),
              ],
            ),
          ),
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(10),
            color: t.bgSunken,
            child: Text(
              widget.code,
              style: TextStyle(
                fontFamily: 'monospace',
                fontSize: 11,
                height: 1.5,
                color: t.fgMuted,
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(10),
            child: Text(
              isParse
                  ? '只影响这一张图，其余内容照常显示；修正语法后会自动重新渲染。'
                  : '源码已保留，可复制到其他工具查看；文档其余内容不受影响。',
              style: TextStyle(fontSize: 11.5, color: t.fgSubtle, height: 1.5),
            ),
          ),
        ],
      ),
    );
  }

  Widget _iconBtn(IconData icon, String tip, VoidCallback onTap, AppTokens t) {
    return Tooltip(
      message: tip,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(t.rSm),
        child: Padding(
          padding: const EdgeInsets.all(5),
          child: Icon(icon, size: 16, color: t.fgMuted),
        ),
      ),
    );
  }

  Future<void> _copyCode() async {
    await _copyToClipboard(widget.code);
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('已复制图表源码')),
    );
  }

  void _openViewer() {
    final r = _result;
    if (r == null || !r.ok) return;
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => MermaidViewerPage(
          svg: r.svg,
          code: widget.code,
          kind: _kind,
        ),
      ),
    );
  }
}

/// 骨架占位（固定高度，避免成图时布局跳动）
class _Skeleton extends StatelessWidget {
  const _Skeleton({required this.tokens});
  final AppTokens tokens;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 160,
      child: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            SizedBox(
              width: 20,
              height: 20,
              child: CircularProgressIndicator(
                strokeWidth: 2,
                color: AppTokens.brand500,
              ),
            ),
            const SizedBox(height: 10),
            Text('渲染中…', style: TextStyle(fontSize: 11, color: tokens.fgSubtle)),
          ],
        ),
      ),
    );
  }
}

/// 静态 SVG 视图（缓存命中路径：不创建额外 WebView，直接显示已渲染结果）
///
/// 必须是 StatefulWidget：`WebViewController` 的持有成本很高，放在
/// StatelessWidget 的 `build` 里会导致**每次重建都新建一个 WebView**（主题切换、
/// 列表滚动、父级 setState 都会触发），旧控制器又没有任何地方释放，
/// 很快就会堆出大量原生 WebView。
class _StaticSvgView extends StatefulWidget {
  const _StaticSvgView({required this.svg});

  final String svg;

  @override
  State<_StaticSvgView> createState() => _StaticSvgViewState();
}

class _StaticSvgViewState extends State<_StaticSvgView> {
  late final WebViewController _controller;

  @override
  void initState() {
    super.initState();
    _controller = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.disabled)
      ..setBackgroundColor(Colors.transparent)
      ..setNavigationDelegate(
        NavigationDelegate(
          onNavigationRequest: (request) => request.url.startsWith('data:')
              ? NavigationDecision.navigate
              : NavigationDecision.prevent,
        ),
      )
      ..loadHtmlString(_wrap(widget.svg));
  }

  @override
  void didUpdateWidget(covariant _StaticSvgView oldWidget) {
    super.didUpdateWidget(oldWidget);
    // SVG 变了（例如主题切换后重新渲染）才重新加载，避免无谓刷新
    if (oldWidget.svg != widget.svg) {
      _controller.loadHtmlString(_wrap(widget.svg));
    }
  }

  @override
  Widget build(BuildContext context) => WebViewWidget(controller: _controller);

  static String _wrap(String svg) {
    // 尺寸策略（与 Web 端 `.mmd-canvas` 保持一致，别再改回 width:100%）：
    //   · `width:auto`：**不放大**，按 SVG 自己的固有尺寸画。写成
    //     `width:100% !important` 会把窄图横向拉变形 —— 类图自然宽只有 ~131，
    //     被拉到 360 宽后方框与文字严重横向变形（Web 端实测并修过同一个坑）。
    //   · `max-width:100%`：**必要时等比缩小**，保证整张图一定落在屏宽内。
    //     渲染时已按展示宽度排版过，正常不会触发；但绝不能让它溢出：
    //     `max-width:none` 配 `margin:0 auto` 会让超宽图的左右两侧都跑到可视区外，
    //     且 `scrollWidth` 仍等于屏宽 —— **横滑也够不到**，实测就是这样把甘特图
    //     "藏"起来的（用户反馈「无法显示甘特图」）。
    //   · `height:auto`：保持宽高比；`margin:0 auto`：窄图居中。
    //   · viewport meta：让 WebView 用设备宽度做布局视口，避免整页被缩放。
    const style = '<style>html,body{margin:0;padding:0;background:transparent;'
        'overflow:hidden}svg{display:block;margin:0 auto;width:auto!important;'
        'max-width:100%;height:auto!important}</style>';
    return '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '$style</head><body>$svg</body></html>';
  }
}

/// 复制到剪贴板（避免在多个页面重复实现）
Future<void> _copyToClipboard(String text) async {
  await Clipboard.setData(ClipboardData(text: text));
}

/// 全屏查看器（SVG 原始矢量，可缩放）
class MermaidViewerPage extends StatefulWidget {
  const MermaidViewerPage({
    super.key,
    required this.svg,
    required this.code,
    required this.kind,
  });

  final String svg;
  final String code;
  final String kind;

  @override
  State<MermaidViewerPage> createState() => _MermaidViewerPageState();
}

class _MermaidViewerPageState extends State<MermaidViewerPage> {
  late final WebViewController _controller;

  @override
  void initState() {
    super.initState();
    _controller = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.disabled)
      ..setBackgroundColor(Colors.white)
      ..setNavigationDelegate(
        NavigationDelegate(
          onNavigationRequest: (request) => request.url.startsWith('data:')
              ? NavigationDecision.navigate
              : NavigationDecision.prevent,
        ),
      )
      ..loadHtmlString(_viewerHtml(widget.svg));
  }

  static String _viewerHtml(String svg) {
    // 全屏查看器：同样不能 `width:100%` 无条件拉伸（窄图会横向变形）。
    // 这里允许 `max-width:100%` 等比缩小到屏幕宽度（全屏场景没有滚动条可用），
    // 但不放大 —— 与 Web 端 `.mmd-viewer-paper svg` 的策略一致。
    const style = '<style>html,body{margin:0;padding:16px;background:#fff}'
        'svg{display:block;margin:0 auto;width:auto!important;max-width:100%;height:auto!important}'
        '</style>';
    return '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '$style</head><body>$svg</body></html>';
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    return Scaffold(
      backgroundColor: t.bgCanvas,
      appBar: AppBar(
        title: Text('${widget.kind} 图表'),
        actions: [
          IconButton(
            tooltip: '复制源码',
            icon: const Icon(Icons.copy_rounded),
            onPressed: () async {
              await _copyToClipboard(widget.code);
              if (!context.mounted) return;
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(content: Text('已复制图表源码')),
              );
            },
          ),
        ],
      ),
      body: InteractiveViewer(
        minScale: 0.5,
        maxScale: 6,
        child: SizedBox(
          width: MediaQuery.of(context).size.width,
          child: WebViewWidget(controller: _controller),
        ),
      ),
    );
  }
}
