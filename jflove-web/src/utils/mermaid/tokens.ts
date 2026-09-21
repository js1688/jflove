/**
 * Mermaid 主题变量：设计令牌 → mermaid 变量名（亮/暗各一套字面量）
 *
 * ⚠ 关键实现约束（实测得出，别踩坑）：
 *   mermaid v11 会对 themeVariables 里的颜色**做运算**（生成 `<style>` 规则、
 *   处理透明度、计算对比色），因此**不能传 `var(--xxx)`** —— 传了会直接抛
 *   `Unsupported color format: "var(--brand-50)"`，整张图渲染失败。
 *
 *   所以这里必须给出**字面量色值**，并且亮/暗各一套；配套结论是：
 *   **主题切换必须重新渲染图表**（不能靠 CSS 跟随），这条已写入设计文档 §4.1。
 *
 *   本文件的色值必须与 `src/styles/tokens.css` 保持同步 ——
 *   改动令牌颜色时两处都要改（`tokens.css` 是唯一真相，这里是它在 mermaid 里的投影）。
 *
 * 已知例外：饼图分区色等位置 mermaid 会做插值，本就要求字面量，一并放在这里。
 */

export type ThemeName = 'light' | 'dark';

/** 亮色主题下 mermaid 所需的全部色值 */
const LIGHT = {
  // 主色系（主节点）
  primaryColor: '#eef2ff', // --brand-50
  primaryBorderColor: '#6366f1', // --brand-500
  primaryTextColor: '#312e81', // --brand-900
  secondaryColor: '#f1f5f9', // --neutral-100
  secondaryBorderColor: '#cbd5e1', // --neutral-300
  secondaryTextColor: '#64748b', // --fg-muted
  tertiaryColor: '#fff1f2', // --accent-50
  tertiaryBorderColor: '#f43f5e', // --accent-500
  tertiaryTextColor: '#9f1239',
  // 连线与文字
  lineColor: '#cbd5e1', // --border-strong
  textColor: '#64748b', // --fg-muted
  titleColor: '#0f172a', // --neutral-900
  edgeLabelBackground: '#ffffff', // --bg-surface
  // 容器
  clusterBkg: '#f8fafc', // --neutral-50
  clusterBorder: '#e2e8f0', // --neutral-200
  // 时序图
  actorBkg: '#eef2ff',
  actorBorder: '#6366f1',
  actorTextColor: '#312e81',
  actorLineColor: '#cbd5e1',
  signalColor: '#64748b',
  signalTextColor: '#64748b',
  labelBoxBkgColor: '#eef2ff',
  labelBoxBorderColor: '#a5b4fc',
  labelTextColor: '#312e81',
  loopTextColor: '#64748b',
  noteBkgColor: '#fffbeb', // --warning-50
  noteBorderColor: '#f59e0b', // --warning-500
  noteTextColor: '#64748b',
  activationBkgColor: '#e0e7ff', // --brand-100
  activationBorderColor: '#6366f1',
  // 状态/类图
  labelBackgroundColor: '#ffffff',
  altBackground: '#f8fafc',
  // 甘特图
  sectionBkgColor: '#eef2ff',
  altSectionBkgColor: '#f8fafc',
  sectionBkgColor2: '#fff1f2',
  taskBkgColor: '#6366f1',
  taskTextColor: '#ffffff',
  taskTextOutsideColor: '#64748b',
  taskTextLightColor: '#ffffff',
  taskTextDarkColor: '#0f172a',
  gridColor: '#e2e8f0',
  doneTaskBkgColor: '#e2e8f0',
  doneTaskBorderColor: '#cbd5e1',
  critBorderColor: '#f43f5e',
  critBkgColor: '#f43f5e',
  todayLineColor: '#f43f5e',
  // 饼图
  pieTitleTextColor: '#0f172a',
  pieSectionTextColor: '#ffffff',
  pieLegendTextColor: '#64748b',
  pieOuterStrokeColor: '#e2e8f0',
  // ER 图
  attributeBackgroundColorOdd: '#ffffff',
  attributeBackgroundColorEven: '#f8fafc',
} as const;

/** 暗色主题（在亮色基础上覆盖差异项，语义与 tokens.css 的 `.dark` 一致） */
const DARK: Record<keyof typeof LIGHT, string> = {
  primaryColor: '#1b2440', // 深底上的品牌节点（brand-500 @ 18% 叠在 surface 上）
  primaryBorderColor: '#818cf8', // --brand-400
  primaryTextColor: '#c7d2fe', // --brand-200
  secondaryColor: '#111c31',
  secondaryBorderColor: '#334155', // --neutral-700
  secondaryTextColor: '#94a3b8', // --fg-muted (dark)
  tertiaryColor: '#2a1721',
  tertiaryBorderColor: '#fb7185',
  tertiaryTextColor: '#fda4af', // --accent-300
  lineColor: '#475569', // --neutral-600（暗色下连线需要更亮才可见）
  textColor: '#94a3b8',
  titleColor: '#e8edf7',
  edgeLabelBackground: '#0f172a', // --bg-surface (dark)
  clusterBkg: '#0b1220', // --bg-sunken (dark)
  clusterBorder: 'rgba(255,255,255,0.10)', // --border-default (dark)
  actorBkg: '#1b2440',
  actorBorder: '#818cf8',
  actorTextColor: '#c7d2fe',
  actorLineColor: '#475569',
  signalColor: '#94a3b8',
  signalTextColor: '#94a3b8',
  labelBoxBkgColor: '#1b2440',
  labelBoxBorderColor: '#4f46e5',
  labelTextColor: '#c7d2fe',
  loopTextColor: '#94a3b8',
  noteBkgColor: '#1e293b', // --neutral-800
  noteBorderColor: '#fbbf24',
  noteTextColor: '#cbd5e1',
  activationBkgColor: '#232c47',
  activationBorderColor: '#818cf8',
  labelBackgroundColor: '#0f172a',
  altBackground: '#0b1220',
  sectionBkgColor: '#1b2440',
  altSectionBkgColor: '#0b1220',
  sectionBkgColor2: '#2a1721',
  taskBkgColor: '#6366f1',
  taskTextColor: '#ffffff',
  taskTextOutsideColor: '#94a3b8',
  taskTextLightColor: '#ffffff',
  taskTextDarkColor: '#0f172a',
  gridColor: '#1e293b',
  doneTaskBkgColor: '#1e293b',
  doneTaskBorderColor: '#334155',
  critBorderColor: '#fb7185',
  critBkgColor: '#fb7185',
  todayLineColor: '#fb7185',
  pieTitleTextColor: '#e8edf7',
  pieSectionTextColor: '#0f172a',
  pieLegendTextColor: '#94a3b8',
  pieOuterStrokeColor: '#1e293b',
  attributeBackgroundColorOdd: '#0f172a',
  attributeBackgroundColorEven: '#0b1220',
};

/** 饼图分区色（mermaid 会插值，必须字面量） */
export const PIE_COLORS = [
  '#6366f1',
  '#0ea5e9',
  '#10b981',
  '#f59e0b',
  '#f43f5e',
  '#a855f7',
  '#14b8a6',
  '#f97316',
  '#64748b',
  '#8b5cf6',
] as const;

/** 按主题取色值表 */
export function themeColors(theme: ThemeName): Record<string, string> {
  return theme === 'dark' ? DARK : LIGHT;
}

/** 便于测试：断言两套表键集合一致 */
export const __testing = { LIGHT, DARK, lightKeys: Object.keys(LIGHT), darkKeys: Object.keys(DARK) };
