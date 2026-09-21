/**
 * Mermaid 链路单元测试（v1.5.0 新增核心能力）
 *
 * 覆盖设计文档 §4 里"不靠肉眼"的几项硬约束：
 *   - 图表块切分正确（不吞掉后续内容、大小写不敏感）
 *   - SVG 清洗真的能去掉可执行构造（AC-8 的安全前提）
 *   - 亮/暗两套主题色值完整且互不相同（AC-11 的前提）
 *   - 图表类型识别与错误行号提取
 */
import { describe, expect, it } from 'vitest';
import { hasMermaidBlock, renderMarkdown, splitMermaidBlocks } from '../../src/utils/markdown';
import { sanitizeSvg, svgHasDangerousContent } from '../../src/utils/mermaid/sanitize';
import { buildMermaidConfig } from '../../src/utils/mermaid/config';
import { __testing as tokenTesting, themeColors } from '../../src/utils/mermaid/tokens';
import { detectDiagramKind } from '../../src/utils/mermaid/types';
import { __testing as rendererTesting } from '../../src/utils/mermaid/renderer';

describe('markdown 图表块切分', () => {
  it('把 ```mermaid 块切成独立片段，其余内容仍是 HTML', () => {
    const md = [
      '# 标题',
      '',
      '正文段落。',
      '',
      '```mermaid',
      'flowchart LR',
      '  A --> B',
      '```',
      '',
      '收尾段落。',
    ].join('\n');

    const segs = splitMermaidBlocks(md);

    expect(segs.map((s) => s.kind)).toEqual(['html', 'mermaid', 'html']);
    const mmd = segs[1];
    expect(mmd.kind).toBe('mermaid');
    if (mmd.kind === 'mermaid') {
      expect(mmd.code).toBe('flowchart LR\n  A --> B');
      expect(mmd.index).toBe(0);
    }
    // 前后文字没有被吞掉
    const html = segs.filter((s) => s.kind === 'html').map((s) => (s.kind === 'html' ? s.html : '')).join('');
    expect(html).toContain('标题');
    expect(html).toContain('收尾段落');
  });

  it('语言标记大小写不敏感', () => {
    const segs = splitMermaidBlocks('```Mermaid\nflowchart LR\n  A --> B\n```');
    expect(segs).toHaveLength(1);
    expect(segs[0].kind).toBe('mermaid');
  });

  it('多个图表按出现顺序编号', () => {
    const md = '```mermaid\nA-->B\n```\n\n中间\n\n```mermaid\nC-->D\n```';
    const segs = splitMermaidBlocks(md);
    const idx = segs.filter((s) => s.kind === 'mermaid').map((s) => (s.kind === 'mermaid' ? s.index : -1));
    expect(idx).toEqual([0, 1]);
  });

  it('围栏未闭合时不吞掉后续内容', () => {
    const segs = splitMermaidBlocks('开头\n\n```mermaid\nflowchart LR\n  A --> B');
    // 未闭合 → 当作普通文本（保持原文可读，不丢内容）
    expect(segs.every((s) => s.kind === 'html')).toBe(true);
    const all = segs.map((s) => (s.kind === 'html' ? s.html : '')).join('');
    expect(all).toContain('开头');
  });

  it('hasMermaidBlock 能识别（用于决定是否加载引擎）', () => {
    expect(hasMermaidBlock('```mermaid\nA-->B\n```')).toBe(true);
    expect(hasMermaidBlock('```Mermaid\nA-->B\n```')).toBe(true);
    expect(hasMermaidBlock('```python\nprint(1)\n```')).toBe(false);
    expect(hasMermaidBlock('普通文本')).toBe(false);
  });
});

describe('markdown 普通片段渲染', () => {
  it('代码块带 hljs 高亮类名与语言标签', () => {
    const html = renderMarkdown('```python\nprint("hi")\n```');
    expect(html).toContain('code-block');
    expect(html).toContain('language-python');
    expect(html).toContain('hljs');
  });

  it('清洗危险 HTML（script / 事件属性 / javascript 协议）', () => {
    const html = renderMarkdown(
      '<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[点我](javascript:alert(1))',
    );
    expect(html).not.toContain('<script');
    expect(html.toLowerCase()).not.toContain('onerror');
    expect(html).not.toContain('javascript:');
  });

  it('表格与任务清单渲染出结构', () => {
    const table = renderMarkdown('| A | B |\n| --- | --- |\n| 1 | 2 |');
    expect(table).toContain('<table>');
    const tasks = renderMarkdown('- [x] 已完成\n- [ ] 未完成');
    expect(tasks).toContain('type="checkbox"');
  });
});

describe('Mermaid SVG 清洗', () => {
  const dirty = [
    '<svg xmlns="http://www.w3.org/2000/svg">',
    '<script>alert(1)</script>',
    '<foreignObject><div onclick="evil()">文本</div></foreignObject>',
    '<a href="javascript:alert(1)"><text>bad</text></a>',
    '<image xlink:href="http://evil.example/x.png"/>',
    '<rect width="10" height="10" onmouseover="evil()"/>',
    '<circle r="5"/>',
    '</svg>',
  ].join('');

  it('移除 script / foreignObject / 事件属性 / 危险协议 / 外部引用', () => {
    const clean = sanitizeSvg(dirty);
    expect(clean).not.toContain('<script');
    expect(clean).not.toContain('foreignObject');
    expect(clean.toLowerCase()).not.toContain('onclick');
    expect(clean.toLowerCase()).not.toContain('onmouseover');
    expect(clean).not.toContain('javascript:');
    expect(clean).not.toContain('http://evil.example');
    // 正常图形保留
    expect(clean).toContain('<circle r="5"');
  });

  it('svgHasDangerousContent 能作为断言工具', () => {
    expect(svgHasDangerousContent(dirty)).toBe(true);
    expect(svgHasDangerousContent(sanitizeSvg(dirty))).toBe(false);
  });

  it('清洗不破坏正常 SVG 结构', () => {
    const ok = '<svg viewBox="0 0 10 10"><rect width="10" height="10"/><text x="1" y="1">中文</text></svg>';
    expect(sanitizeSvg(ok)).toBe(ok);
  });
});

describe('Mermaid 主题配置', () => {
  it('亮/暗两套色值键集合一致（改一处不能漏另一处）', () => {
    expect(tokenTesting.darkKeys.sort()).toEqual(tokenTesting.lightKeys.sort());
  });

  it('亮/暗关键色确实不同（保证 AC-11 有实际效果）', () => {
    const l = themeColors('light');
    const d = themeColors('dark');
    expect(l.primaryColor).not.toBe(d.primaryColor);
    expect(l.lineColor).not.toBe(d.lineColor);
    expect(l.edgeLabelBackground).not.toBe(d.edgeLabelBackground);
  });

  it('配置里不含 var(--x)（mermaid 会对颜色做运算，传变量会抛错）', () => {
    const cfg = buildMermaidConfig('light');
    const json = JSON.stringify(cfg);
    expect(json).not.toContain('var(--');
  });

  it('securityLevel 必须是 strict（安全宪法）', () => {
    expect(buildMermaidConfig('light').securityLevel).toBe('strict');
    expect(buildMermaidConfig('dark').securityLevel).toBe('strict');
  });

  it('流程图关闭 htmlLabels（减少 foreignObject，便于清洗与降级）', () => {
    expect(buildMermaidConfig('light').flowchart.htmlLabels).toBe(false);
  });

  it('htmlLabels:false 必须在配置**顶层**（否则四类图的文字会落在 foreignObject 里被清洗掉）', () => {
    // 回归锁：这条是 v1.5.0 的严重缺陷根因。
    // mermaid 11.16.0 实测（设计预览/verify_mermaid_labels.mjs）：只写
    // `flowchart:{htmlLabels:false}` 时，flowchart / state / class / ER 的节点与
    // 边标签仍然输出为 <foreignObject>（flow fo=4、er fo=10）；把 htmlLabels:false
    // 同时写到配置顶层后，七类图的 foreignObject 全部为 0、文字全部在 <text> 内。
    // 少了这条，sanitizeSvg 会把这些图的文字整体删除，只剩空框与连线。
    expect(buildMermaidConfig('light').htmlLabels).toBe(false);
    expect(buildMermaidConfig('dark').htmlLabels).toBe(false);
  });
});

describe('图表类型识别与错误提取', () => {
  it('识别常见图表类型', () => {
    expect(detectDiagramKind('flowchart LR\n A-->B')).toBe('Flowchart');
    expect(detectDiagramKind('graph TD\n A-->B')).toBe('Flowchart');
    expect(detectDiagramKind('sequenceDiagram\n A->>B: hi')).toBe('Sequence');
    expect(detectDiagramKind('gantt\n title x')).toBe('Gantt');
    expect(detectDiagramKind('pie\n "a": 1')).toBe('Pie');
    expect(detectDiagramKind('stateDiagram-v2\n [*] --> A')).toBe('State');
    expect(detectDiagramKind('classDiagram\n A --> B')).toBe('Class');
    expect(detectDiagramKind('erDiagram\n A ||--o{ B : has')).toBe('ER');
  });

  it('忽略注释行后取首行', () => {
    expect(detectDiagramKind('%% 注释\nflowchart LR\n A-->B')).toBe('Flowchart');
  });

  it('空内容返回兜底类型', () => {
    expect(detectDiagramKind('')).toBe('Mermaid');
    expect(detectDiagramKind('%% 只有注释')).toBe('Mermaid');
  });

  it('从 mermaid 错误对象提取 1-based 行号', () => {
    const err = { hash: { loc: { first_line: 2 } }, message: 'Parse error\n更多细节' };
    expect(rendererTesting.extractParseError(err)).toEqual({ line: 3, message: 'Parse error' });
  });

  it('无位置信息时行号为 0（前端展示"无法定位行号"）', () => {
    const r = rendererTesting.extractParseError(new Error('boom'));
    expect(r.line).toBe(0);
    expect(r.message).toBe('boom');
  });

  it('SVG 尺寸从 viewBox 解析（决定占位高度，防跳动）', () => {
    const svg = '<svg viewBox="-10 -20 320 180"><g/></svg>';
    expect(rendererTesting.measureSvg(svg)).toEqual({ width: 320, height: 180 });
  });
});
