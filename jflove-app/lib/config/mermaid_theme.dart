import 'package:flutter/material.dart';

/// Mermaid 主题变量（移动端）
///
/// ⚠ 必须是**颜色字面量**：mermaid 会对 themeVariables 做运算（生成 `<style>`
/// 规则、处理透明度、推导对比色），传 CSS 变量或非颜色值会直接抛
/// `Unsupported color format` —— 这是 Web 与桌面端实现期都实测到的结论。
/// 因此这里给出亮/暗两套字面量，并与 `config/design_tokens.dart` 的取值对齐。
///
/// 另一条约束：`fontFamily` 里**不能有双引号**。`jsonEncode` 会把
/// `"sans-serif"` 转义成 `\"sans-serif\"`，而 `\"` 在 JSON 字符串里非法，
/// 会让页面侧解析报错且信息指向图表源码，极难定位。
@immutable
class MermaidTheme {
  const MermaidTheme._(this.name, this._vars);

  final String name;
  final Map<String, Object> _vars;

  static const String _fontFamily =
      'Inter, Segoe UI, PingFang SC, Microsoft YaHei, sans-serif';

  /// 亮色：与 AppTokens.light 同色系
  static const MermaidTheme light = MermaidTheme._('light', {
    'darkMode': false,
    'fontFamily': _fontFamily,
    'fontSize': '13px',
    'background': '#ffffff',
    // 主 / 次 / 三级节点
    'primaryColor': '#eef2ff',
    'primaryBorderColor': '#6366f1',
    'primaryTextColor': '#312e81',
    'mainBkg': '#eef2ff',
    'nodeBorder': '#6366f1',
    'nodeTextColor': '#312e81',
    'secondaryColor': '#f1f5f9',
    'secondaryBorderColor': '#cbd5e1',
    'secondaryTextColor': '#64748b',
    'tertiaryColor': '#fff1f2',
    'tertiaryBorderColor': '#f43f5e',
    'tertiaryTextColor': '#9f1239',
    // 连线与文字
    'lineColor': '#cbd5e1',
    'textColor': '#64748b',
    'titleColor': '#0f172a',
    'edgeLabelBackground': '#ffffff',
    // 容器
    'clusterBkg': '#f8fafc',
    'clusterBorder': '#e2e8f0',
    // 时序图
    'actorBkg': '#eef2ff',
    'actorBorder': '#6366f1',
    'actorTextColor': '#312e81',
    'actorLineColor': '#cbd5e1',
    'signalColor': '#64748b',
    'signalTextColor': '#64748b',
    'labelBoxBkgColor': '#eef2ff',
    'labelBoxBorderColor': '#a5b4fc',
    'labelTextColor': '#312e81',
    'loopTextColor': '#64748b',
    'noteBkgColor': '#fffbeb',
    'noteBorderColor': '#f59e0b',
    'noteTextColor': '#64748b',
    'activationBkgColor': '#e0e7ff',
    'activationBorderColor': '#6366f1',
    // 状态 / 类图
    'labelBackgroundColor': '#ffffff',
    'altBackground': '#f8fafc',
    // 甘特图
    'sectionBkgColor': '#eef2ff',
    'altSectionBkgColor': '#f8fafc',
    'sectionBkgColor2': '#fff1f2',
    'taskBkgColor': '#6366f1',
    'taskTextColor': '#ffffff',
    'taskTextOutsideColor': '#64748b',
    'taskTextLightColor': '#ffffff',
    'taskTextDarkColor': '#0f172a',
    'gridColor': '#e2e8f0',
    'doneTaskBkgColor': '#e2e8f0',
    'doneTaskBorderColor': '#cbd5e1',
    'critBorderColor': '#f43f5e',
    'critBkgColor': '#f43f5e',
    'todayLineColor': '#f43f5e',
    // 饼图（mermaid 会插值，必须字面量）
    'pie1': '#6366f1',
    'pie2': '#0ea5e9',
    'pie3': '#10b981',
    'pie4': '#f59e0b',
    'pie5': '#f43f5e',
    'pie6': '#a855f7',
    'pie7': '#14b8a6',
    'pie8': '#f97316',
    'pie9': '#64748b',
    'pie10': '#8b5cf6',
    'pieOpacity': '0.9',
    'pieOuterStrokeColor': '#e2e8f0',
    'pieTitleTextColor': '#0f172a',
    'pieSectionTextColor': '#ffffff',
    'pieLegendTextColor': '#64748b',
    // ER 图
    'attributeBackgroundColorOdd': '#ffffff',
    'attributeBackgroundColorEven': '#f8fafc',
  });

  /// 暗色：与 AppTokens.dark 同色系
  static const MermaidTheme dark = MermaidTheme._('dark', {
    'darkMode': true,
    'fontFamily': _fontFamily,
    'fontSize': '13px',
    'background': '#0f172a',
    'primaryColor': '#1b2440',
    'primaryBorderColor': '#818cf8',
    'primaryTextColor': '#c7d2fe',
    'mainBkg': '#1b2440',
    'nodeBorder': '#818cf8',
    'nodeTextColor': '#c7d2fe',
    'secondaryColor': '#111c31',
    'secondaryBorderColor': '#334155',
    'secondaryTextColor': '#94a3b8',
    'tertiaryColor': '#2a1721',
    'tertiaryBorderColor': '#fb7185',
    'tertiaryTextColor': '#fda4af',
    'lineColor': '#475569',
    'textColor': '#94a3b8',
    'titleColor': '#e8edf7',
    'edgeLabelBackground': '#0f172a',
    'clusterBkg': '#0b1220',
    'clusterBorder': 'rgba(255,255,255,0.10)',
    'actorBkg': '#1b2440',
    'actorBorder': '#818cf8',
    'actorTextColor': '#c7d2fe',
    'actorLineColor': '#475569',
    'signalColor': '#94a3b8',
    'signalTextColor': '#94a3b8',
    'labelBoxBkgColor': '#1b2440',
    'labelBoxBorderColor': '#4f46e5',
    'labelTextColor': '#c7d2fe',
    'loopTextColor': '#94a3b8',
    'noteBkgColor': '#1e293b',
    'noteBorderColor': '#fbbf24',
    'noteTextColor': '#cbd5e1',
    'activationBkgColor': '#232c47',
    'activationBorderColor': '#818cf8',
    'labelBackgroundColor': '#0f172a',
    'altBackground': '#0b1220',
    'sectionBkgColor': '#1b2440',
    'altSectionBkgColor': '#0b1220',
    'sectionBkgColor2': '#2a1721',
    'taskBkgColor': '#6366f1',
    'taskTextColor': '#ffffff',
    'taskTextOutsideColor': '#94a3b8',
    'taskTextLightColor': '#ffffff',
    'taskTextDarkColor': '#0f172a',
    'gridColor': '#1e293b',
    'doneTaskBkgColor': '#1e293b',
    'doneTaskBorderColor': '#334155',
    'critBorderColor': '#fb7185',
    'critBkgColor': '#fb7185',
    'todayLineColor': '#fb7185',
    'pie1': '#6366f1',
    'pie2': '#0ea5e9',
    'pie3': '#10b981',
    'pie4': '#f59e0b',
    'pie5': '#f43f5e',
    'pie6': '#a855f7',
    'pie7': '#14b8a6',
    'pie8': '#f97316',
    'pie9': '#64748b',
    'pie10': '#8b5cf6',
    'pieOpacity': '0.9',
    'pieOuterStrokeColor': '#1e293b',
    'pieTitleTextColor': '#e8edf7',
    'pieSectionTextColor': '#0f172a',
    'pieLegendTextColor': '#94a3b8',
    'attributeBackgroundColorOdd': '#0f172a',
    'attributeBackgroundColorEven': '#0b1220',
  });

  /// 按主题亮度取
  static MermaidTheme of(Brightness brightness) =>
      brightness == Brightness.dark ? dark : light;

  /// 供渲染页注入的载荷（字体 + 变量）
  Map<String, Object> toPayload() => {
        'fontFamily': _fontFamily,
        'vars': _vars,
      };

  /// 便于测试断言
  Map<String, Object> get vars => _vars;
}
