/**
 * Mermaid 几何修正单元测试（v1.5.0 反馈修复）
 *
 * 背景（实测数据见 `设计预览/find_clipped_text.mjs`、`probe_reduced_rule_which.mjs`）：
 * mermaid 给的 `viewBox` 有时**装不下它自己画的内容**，而 SVG 默认 `overflow:hidden`
 * ⇒ 文字被裁；更糟的是**导出/下载的 SVG 就是这同一份字符串**，于是"显示不全"必然
 * 意味着"下载的图片也不全"。
 *
 * 这里锁住三件事：
 *   1. viewBox 会被撑到装下内容，且**只放不缩**（本来能看见的不能反被裁掉）；
 *   2. mermaid 故意画超长的辅助线（时序图生命线 y2=2000、甘特图今日线 x=28399）
 *      必须被排除，否则图会被撑成一大片空白；
 *   3. 调用方必须**先把 SVG 插进 DOM** 再量 —— 初版把空容器丢进来量，
 *      于是"重新测量"从未生效、成了死代码（这是当时真正的根因，必须锁死）。
 *
 * jsdom 没有 `getBBox`/`getCTM`，所以用真实的 jsdom 元素 + 打桩几何来测；
 * 真浏览器的端到端验证由 `设计预览/find_clipped_text.mjs` 与
 * `设计预览/verify_web_export_complete.mjs` 负责。
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { __testing as t } from '../../src/utils/mermaid/renderer';

const SVG_NS = 'http://www.w3.org/2000/svg';

/** 单位矩阵（假环境里元素用户空间与 svg 用户空间相同） */
const IDENTITY = { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 };

interface FakeBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** 造一个真实 jsdom `<svg>`，其子元素带打桩的 `getBBox`/`getCTM` */
function makeSvg(opts: {
  viewBox?: string | null;
  width?: string;
  height?: string;
  style?: string;
  texts?: Array<{ text: string; box: FakeBox }>;
  shapes?: Array<{ tag: string; box: FakeBox; inDefs?: boolean }>;
}) {
  const doc = document.implementation.createHTMLDocument('');
  const svg = doc.createElementNS(SVG_NS, 'svg') as SVGSVGElement;
  if (opts.viewBox !== null) svg.setAttribute('viewBox', opts.viewBox ?? '0 0 100 50');
  if (opts.width) svg.setAttribute('width', opts.width);
  if (opts.height) svg.setAttribute('height', opts.height);
  if (opts.style) svg.setAttribute('style', opts.style);

  const stubGeometry = (el: Element, box: FakeBox) => {
    (el as unknown as { getBBox: () => DOMRect }).getBBox = () =>
      ({ ...box, top: box.y, left: box.x, right: box.x + box.width, bottom: box.y + box.height }) as DOMRect;
    (el as unknown as { getCTM: () => unknown }).getCTM = () => IDENTITY;
  };

  (opts.texts ?? []).forEach(({ text, box }) => {
    const node = doc.createElementNS(SVG_NS, 'text');
    node.textContent = text;
    (node as unknown as { closest: () => Element | null }).closest = () => null;
    stubGeometry(node, box);
    svg.appendChild(node);
  });
  (opts.shapes ?? []).forEach(({ tag, box, inDefs }) => {
    const node = doc.createElementNS(SVG_NS, tag);
    (node as unknown as { closest: (sel: string) => Element | null }).closest = () =>
      (inDefs ? ({} as Element) : null);
    stubGeometry(node, box);
    svg.appendChild(node);
  });
  (svg as unknown as { getCTM: () => unknown }).getCTM = () => IDENTITY;

  // 让 querySelector/querySelectorAll 能看到我们挂上去的（jsdom 原生即可）
  return svg;
}

function viewBoxOf(svg: SVGSVGElement): number[] {
  return (svg.getAttribute('viewBox') || '').split(/[\s,]+/).map(Number);
}

describe('measureContentBounds：内容范围量测', () => {
  const VB = { ox: 0, oy: 0, vw: 200, vh: 100 };

  it('文字一律计入（哪怕超出很多），因为文字被裁就是用户说的"显示不全"', () => {
    const svg = makeSvg({ texts: [{ text: '很远的字', box: { x: 900, y: 900, width: 40, height: 20 } }] });
    const b = t.measureContentBounds(svg, VB);
    expect(b).not.toBeNull();
    expect(b!.x2).toBe(940);
    expect(b!.y2).toBe(920);
  });

  it('空白文字的 text 不计入（mermaid 会产生大量空 text 占位）', () => {
    const svg = makeSvg({ texts: [{ text: '   ', box: { x: 900, y: 900, width: 40, height: 20 } }] });
    expect(t.measureContentBounds(svg, VB)).toBeNull();
  });

  it('小幅超出 viewBox 的图形计入（真实内容：ER 实体框右超 52px）', () => {
    const svg = makeSvg({ shapes: [{ tag: 'rect', box: { x: 10, y: 10, width: 242, height: 60 } }] });
    const b = t.measureContentBounds(svg, VB);
    expect(b).not.toBeNull();
    expect(b!.x2).toBe(252);
  });

  it('远超 viewBox 的辅助线排除（时序图生命线 y2=2000，视口才 185）', () => {
    const svg = makeSvg({ shapes: [{ tag: 'line', box: { x: 75, y: 65, width: 0, height: 2000 } }] });
    expect(t.measureContentBounds(svg, { ox: -50, oy: -10, vw: 456, vh: 185 })).toBeNull();
  });

  it('远超 viewBox 的辅助线排除（甘特图今日线 x=28399，视口才 1374）', () => {
    const svg = makeSvg({ shapes: [{ tag: 'line', box: { x: 28399, y: 25, width: 0, height: 94 } }] });
    expect(t.measureContentBounds(svg, { ox: 0, oy: 0, vw: 1374, vh: 144 })).toBeNull();
  });

  it('defs 里的定义块不参与绘制，不计入', () => {
    const svg = makeSvg({ shapes: [{ tag: 'rect', box: { x: 0, y: 0, width: 500, height: 500 }, inDefs: true }] });
    expect(t.measureContentBounds(svg, VB)).toBeNull();
  });
});

describe('fitSvgToContent：尺寸修正', () => {
  it('拿到 null 时返回空串，不抛错（调用方会退回原始字符串）', () => {
    expect(t.fitSvgToContent(null)).toBe('');
  });

  it('内容装得下时尺寸不变（时序图 / 甘特图不应凭空多出白边）', () => {
    const svg = makeSvg({
      viewBox: '0 0 100 50',
      width: '100%',
      style: 'max-width: 100px;',
      texts: [{ text: '在内', box: { x: 10, y: 10, width: 40, height: 20 } }],
    });
    const out = t.fitSvgToContent(svg);
    expect(viewBoxOf(svg)).toEqual([0, 0, 100, 50]);
    // width="100%" 必须换成像素，否则窄图会被横向拉变形
    expect(svg.getAttribute('width')).toBe('100');
    expect(svg.getAttribute('height')).toBe('50');
    // 内联 max-width 会把图压回旧宽度，必须去掉
    expect(svg.getAttribute('style') || '').not.toContain('max-width');
    expect(out).toContain('viewBox="0 0 100 50"');
  });

  it('内容超出时撑开 viewBox，并在超出侧留 2px 余量防止描边被削', () => {
    const svg = makeSvg({
      viewBox: '0 0 100 50',
      texts: [{ text: '超出', box: { x: 10, y: 10, width: 130, height: 80 } }],
    });
    t.fitSvgToContent(svg);
    // 右边界 140 + 2 = 142；下边界 90 + 2 = 92；左/上没超 → 原点不动
    expect(viewBoxOf(svg)).toEqual([0, 0, 142, 92]);
  });

  it('只偏移原点一侧的超出也能覆盖（内容画在左上外边）', () => {
    const svg = makeSvg({
      viewBox: '0 0 100 50',
      texts: [{ text: '左上', box: { x: -30, y: -20, width: 40, height: 20 } }],
    });
    t.fitSvgToContent(svg);
    // 左 -32、上 -22；右取 max(100, 10)=100；下取 max(50, 0)=50
    expect(viewBoxOf(svg)).toEqual([-32, -22, 132, 72]);
  });

  it('viewBox 损坏（引擎量到 0）时用实测内容范围，不让图整张不可见', () => {
    const svg = makeSvg({
      viewBox: '0 0 0 144',
      texts: [{ text: '内容', box: { x: 20, y: 10, width: 200, height: 40 } }],
    });
    t.fitSvgToContent(svg);
    expect(viewBoxOf(svg)).toEqual([18, 8, 204, 44]);
  });

  it('量测抛错时不把渲染搞挂，且保留原 viewBox（宁可尺寸没修好，也不能改坏产物）', () => {
    const svg = makeSvg({ viewBox: '0 0 100 50', width: '100%' });
    (svg as unknown as { querySelectorAll: () => never }).querySelectorAll = () => {
      throw new Error('boom');
    };
    let out = '';
    expect(() => {
      out = t.fitSvgToContent(svg);
    }).not.toThrow();
    expect(viewBoxOf(svg)).toEqual([0, 0, 100, 50]);
    expect(out).toContain('width="100"');
  });

  it('序列化出的字符串可以被独立打开（带 xmlns，供导出成 SVG 文件）', () => {
    const svg = makeSvg({ viewBox: '0 0 100 50' });
    const out = t.fitSvgToContent(svg);
    expect(out.startsWith('<svg')).toBe(true);
    expect(out).toContain('xmlns="http://www.w3.org/2000/svg"');
  });
});

describe('调用契约：必须先插入 DOM 再量（当时故障的真正根因）', () => {
  const src = readFileSync(join(__dirname, '../../src/utils/mermaid/renderer.ts'), 'utf8');

  it('renderer.ts 在调用 fitSvgToContent 之前先把 SVG 写进 holder', () => {
    const insertAt = src.indexOf('holder.innerHTML = rawSvg');
    const fitAt = src.indexOf('fitSvgToContent(svgEl)');
    expect(insertAt, 'renderer.ts 里找不到 holder.innerHTML = rawSvg').toBeGreaterThan(-1);
    expect(fitAt, 'renderer.ts 里找不到 fitSvgToContent(svgEl)').toBeGreaterThan(-1);
    // 初版就是漏了这一步：把空容器丢给测量函数，于是量不到任何东西，
    // 归一化静默失效 —— 顺序错了等于没有这个安全网
    expect(insertAt).toBeLessThan(fitAt);
  });

  it('不再保留那个"对着空容器 querySelector"的旧入口', () => {
    expect(src).not.toContain('normalizeSvgSize');
  });

  it('renderer.ts 不再引入 HTML 标签转换（那条路线的前提已被证伪）', () => {
    // 路线 A（htmlLabels:true + foreignObject 转 <text>）的前提是
    // "类图在 htmlLabels:false 下排版是引擎缺陷"，而实测真实原因是
    // index.css 的 transition 冻结（见 index.css 注释）。
    // 锁死这一点，避免以后又照着旧结论把转换层加回来。
    expect(src).not.toContain('convertHtmlLabels');
    const cfg = readFileSync(join(__dirname, '../../src/utils/mermaid/config.ts'), 'utf8');
    expect(cfg).not.toContain('htmlLabels: true');
  });
});

describe('reduced-motion 与引擎几何（v1.5.0 最隐蔽的一个坑）', () => {
  const css = readFileSync(join(__dirname, '../../src/index.css'), 'utf8');

  it('reduced-motion 规则不得把 transition 冻结施加到 SVG 子树', () => {
    // 实测：`*{transition-duration:.01ms!important}` 在 reduce 生效时会改变
    // mermaid 的几何（类图外框只按标题画、ER/流程图文字被裁）。
    // 断言过渡冻结带上了 :not(svg):not(svg *) 豁免。
    expect(css).toContain('prefers-reduced-motion');
    expect(css).toMatch(/transition-duration[^;]*!important/);
    expect(css).toContain(':not(svg)');
    expect(css).toContain(':not(svg *)');
  });
});
