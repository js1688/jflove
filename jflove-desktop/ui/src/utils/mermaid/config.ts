/**
 * Mermaid 配置构造 —— 由设计令牌（字面量投影）生成 themeVariables
 *
 * 为什么要显式给 themeVariables（而不是用 mermaid 内置主题）：
 *   内置主题配色与 JFLove 品牌色无关，画出来是"另一个产品"的图。
 *
 * ⚠ 为什么必须是字面量而不是 `var(--x)`：
 *   mermaid v11 会对这些颜色做运算，传 CSS 变量会抛
 *   `Unsupported color format`。因此亮/暗各一套色值，且**主题切换需重新渲染**
 *   （见设计文档 §4.1 与该结论的实测记录）。
 */
import { PIE_COLORS, themeColors, type ThemeName } from './tokens';

/** 与 `--font-sans` 一致的字体栈（mermaid 需要具体字符串，不能是 var()） */
export const MERMAID_FONT_SANS =
  "'Inter','Segoe UI Variable Text','Segoe UI',-apple-system,'PingFang SC','Microsoft YaHei UI','Microsoft YaHei',sans-serif";

/**
 * 构造 mermaid 初始化配置。
 *
 * @param theme 当前主题（决定整套 themeVariables）
 */
export function buildMermaidConfig(theme: ThemeName) {
  const c = themeColors(theme);

  return {
    startOnLoad: false,
    // 安全宪法要求：禁 HTML 标签、禁 javascript: 链接（AGENTS.md §9.4 / §9.6）
    securityLevel: 'strict' as const,
    theme: 'base',
    /**
     * `htmlLabels` 必须是 **false**，且**必须写在配置顶层**（写在 `flowchart: {}` 里不生效）。
     *
     * mermaid 11.16.0 实测（`设计预览/verify_mermaid_labels.mjs`）：
     *   - 只写 `flowchart:{htmlLabels:false}`：flowchart / state / class / ER 的标签仍落在
     *     `<foreignObject>` 里（flow fo=4、er fo=10）；
     *   - 顶层再写一次 `htmlLabels:false`：七类图 **foreignObject 全为 0**，文字都在 `<text>` 内。
     *
     * 为什么必须没有 `foreignObject`：里面是 HTML 而不是 SVG。
     * (a) 我们为了安全必须把它删掉 → 图变成空框加连线；
     * (b) 桌面端 `QSvgRenderer` 直接丢弃其内容 → 同样丢字；
     * (c) 导出成独立 SVG 文件后在别的软件里打开也画不出来。
     *
     * ⚠ v1.5.0 一度想改成 `true`（因为当时以为类图在 false 下排版是引擎缺陷）。
     * **那个结论是错的**：真实原因是本项目 `index.css` 里一条
     * `*{transition-duration:.01ms!important}` 在 `prefers-reduced-motion: reduce` 生效时
     * 改变了引擎的几何（详见 `index.css` 的注释与 `设计预览/probe_reduced_rule_which.mjs`）。
     * 修掉那条 CSS 之后，`false` 在七类图上排版全部正确（0 处裁切、0 个节点框装不下文字），
     * 因此**不需要**"HTML 标签 + 事后转 SVG"的那套机制（它还会让引擎的 viewBox 膨胀约 10 倍）。
     */
    htmlLabels: false,
    fontFamily: MERMAID_FONT_SANS,
    themeVariables: {
      darkMode: theme === 'dark',
      fontFamily: MERMAID_FONT_SANS,
      fontSize: '14px',
      background: c.edgeLabelBackground,

      // 主 / 次 / 三级节点
      primaryColor: c.primaryColor,
      primaryBorderColor: c.primaryBorderColor,
      primaryTextColor: c.primaryTextColor,
      mainBkg: c.primaryColor,
      nodeBorder: c.primaryBorderColor,
      nodeTextColor: c.primaryTextColor,
      secondaryColor: c.secondaryColor,
      secondaryBorderColor: c.secondaryBorderColor,
      secondaryTextColor: c.secondaryTextColor,
      tertiaryColor: c.tertiaryColor,
      tertiaryBorderColor: c.tertiaryBorderColor,
      tertiaryTextColor: c.tertiaryTextColor,

      // 连线与文字
      lineColor: c.lineColor,
      textColor: c.textColor,
      titleColor: c.titleColor,
      edgeLabelBackground: c.edgeLabelBackground,

      // 分组容器
      clusterBkg: c.clusterBkg,
      clusterBorder: c.clusterBorder,

      // 时序图
      actorBkg: c.actorBkg,
      actorBorder: c.actorBorder,
      actorTextColor: c.actorTextColor,
      actorLineColor: c.actorLineColor,
      signalColor: c.signalColor,
      signalTextColor: c.signalTextColor,
      labelBoxBkgColor: c.labelBoxBkgColor,
      labelBoxBorderColor: c.labelBoxBorderColor,
      labelTextColor: c.labelTextColor,
      loopTextColor: c.loopTextColor,
      noteBkgColor: c.noteBkgColor,
      noteBorderColor: c.noteBorderColor,
      noteTextColor: c.noteTextColor,
      activationBkgColor: c.activationBkgColor,
      activationBorderColor: c.activationBorderColor,

      // 状态图 / 类图
      labelBackgroundColor: c.labelBackgroundColor,
      altBackground: c.altBackground,

      // 甘特图
      sectionBkgColor: c.sectionBkgColor,
      altSectionBkgColor: c.altSectionBkgColor,
      sectionBkgColor2: c.sectionBkgColor2,
      taskBkgColor: c.taskBkgColor,
      taskTextColor: c.taskTextColor,
      taskTextOutsideColor: c.taskTextOutsideColor,
      taskTextLightColor: c.taskTextLightColor,
      taskTextDarkColor: c.taskTextDarkColor,
      gridColor: c.gridColor,
      doneTaskBkgColor: c.doneTaskBkgColor,
      doneTaskBorderColor: c.doneTaskBorderColor,
      critBorderColor: c.critBorderColor,
      critBkgColor: c.critBkgColor,
      todayLineColor: c.todayLineColor,

      // 饼图
      pie1: PIE_COLORS[0],
      pie2: PIE_COLORS[1],
      pie3: PIE_COLORS[2],
      pie4: PIE_COLORS[3],
      pie5: PIE_COLORS[4],
      pie6: PIE_COLORS[5],
      pie7: PIE_COLORS[6],
      pie8: PIE_COLORS[7],
      pie9: PIE_COLORS[8],
      pie10: PIE_COLORS[9],
      pieOpacity: '0.9',
      pieOuterStrokeColor: c.pieOuterStrokeColor,
      pieTitleTextColor: c.pieTitleTextColor,
      pieSectionTextColor: c.pieSectionTextColor,
      pieLegendTextColor: c.pieLegendTextColor,

      // ER 图
      attributeBackgroundColorOdd: c.attributeBackgroundColorOdd,
      attributeBackgroundColorEven: c.attributeBackgroundColorEven,
    },

    flowchart: { curve: 'basis', useMaxWidth: true, padding: 12, htmlLabels: false },
    sequence: { useMaxWidth: true, mirrorActors: false, actorMargin: 56, boxMargin: 8 },
    gantt: { useMaxWidth: true, barHeight: 22, topPadding: 46, fontSize: 12 },
    er: { useMaxWidth: true },
    pie: { useMaxWidth: true },
    state: { useMaxWidth: true },
    class: { useMaxWidth: true },
  };
}

export { PIE_COLORS };
