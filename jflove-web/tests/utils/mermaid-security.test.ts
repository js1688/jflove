/**
 * Mermaid 渲染链路安全用例（v1.5.0，Web 端）
 *
 * 对照 `AGENTS.md §9`：
 *   - §9.4：图表源码属于笔记正文 → **缓存键与 console 不得出现明文**；
 *   - §9.6 / §9.7 新增自查项：图表渲染不得成为新的注入面 ——
 *     恶意图表源码（`<script>` / `onerror=` / `javascript:` / 外部回连图片）
 *     经渲染链路后，产物中不得残留可执行脚本或危险协议。
 *
 * 本文件只覆盖「安全」，渲染调度行为见 `mermaid-render.test.ts`。
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

import { renderMarkdown, splitMermaidBlocks } from '../../src/utils/markdown';
import { buildMermaidConfig } from '../../src/utils/mermaid/config';
import { sanitizeSvg, svgHasDangerousContent } from '../../src/utils/mermaid/sanitize';
import { cacheKeyOf } from '../../src/utils/mermaid/renderer';
import { PIE_COLORS } from '../../src/utils/mermaid/tokens';

/** 恶意图表源码：四类注入面齐全 */
const EVIL_SOURCE = [
  'flowchart LR',
  '  A["<script>alert(1)</script>"]',
  '  B["<img src=http://evil.example/x.png onerror=alert(1)>"]',
  '  C["[点我](javascript:alert(2))"]',
  '  A --> B --> C',
].join('\n');

/** mermaid 在 strict 模式下仍可能产出的危险 SVG 片段 */
const EVIL_SVG = [
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">',
  '<script>alert(1)</script>',
  '<foreignObject><div onclick="evil()">x</div></foreignObject>',
  '<iframe src="http://evil.example/frame"></iframe>',
  '<a href="javascript:alert(1)"><text>bad</text></a>',
  '<image xlink:href="http://evil.example/x.png"/>',
  '<rect width="10" height="10" onmouseover="evil()"/>',
  '<circle r="5"/>',
  '</svg>',
].join('');

describe('mermaid 渲染配置的安全基线', () => {
  it('亮/暗两套配置都是 securityLevel=strict（禁 HTML 标签与脚本链接）', () => {
    expect(buildMermaidConfig('light').securityLevel).toBe('strict');
    expect(buildMermaidConfig('dark').securityLevel).toBe('strict');
  });

  it('flowchart 关闭 htmlLabels（节点标签不进 foreignObject）', () => {
    expect(buildMermaidConfig('light').flowchart.htmlLabels).toBe(false);
    expect(buildMermaidConfig('dark').flowchart.htmlLabels).toBe(false);
  });

  it('themeVariables 里没有 CSS 变量（mermaid 会对颜色做运算，传 var() 直接抛错）', () => {
    for (const theme of ['light', 'dark'] as const) {
      expect(JSON.stringify(buildMermaidConfig(theme))).not.toContain('var(--');
    }
  });

  it('渲染配置不含任何远程资源地址（图表不回连网络）', () => {
    const json = JSON.stringify(buildMermaidConfig('dark'));
    expect(json).not.toMatch(/https?:\/\//);
  });
});

describe('恶意图表源码的产物清洗', () => {
  it('mermaid 块被摘出 HTML 化流程，源码里的 <script> 不会变成可执行标签', () => {
    const md = ['# 标题', '', '```mermaid', EVIL_SOURCE, '```', '', '收尾段落。'].join('\n');
    const segs = splitMermaidBlocks(md);

    // mermaid 片段只承载源码字符串（数据），不参与 HTML 化
    const mmd = segs.find((s) => s.kind === 'mermaid');
    expect(mmd).toBeDefined();
    if (mmd?.kind === 'mermaid') {
      expect(mmd.code).toBe(EVIL_SOURCE); // 原样保留，交给 mermaid 当数据处理
    }

    // 普通 HTML 片段里不得出现任何可执行构造
    const html = segs
      .filter((s) => s.kind === 'html')
      .map((s) => (s.kind === 'html' ? s.html : ''))
      .join('');
    expect(html).not.toContain('<script');
    expect(html.toLowerCase()).not.toContain('onerror');
    expect(html).not.toContain('javascript:');
    expect(html).toContain('收尾段落');
  });

  it('渲染后的 SVG 清洗结果不含可执行构造与外部回连', () => {
    const clean = sanitizeSvg(EVIL_SVG);
    expect(svgHasDangerousContent(clean)).toBe(false);
    expect(clean).not.toContain('<script');
    expect(clean).not.toContain('foreignObject');
    expect(clean).not.toContain('<iframe');
    expect(clean.toLowerCase()).not.toContain('onmouseover');
    expect(clean.toLowerCase()).not.toContain('onclick');
    expect(clean).not.toContain('javascript:');
    expect(clean).not.toContain('http://evil.example');
    expect(clean).toContain('<circle r="5"'); // 正常图形不被误删
  });

  it('完整形式的 script 标签一律移除（闭合式 / 单标签 / 大小写混合）', () => {
    for (const dirty of [
      '<svg><script>alert(1)</script></svg>',
      '<svg><SCRIPT>x</SCRIPT></svg>',
      '<svg><script src="http://evil.example/x.js"></script></svg>',
      '<svg><script/></svg>',
    ]) {
      const clean = sanitizeSvg(dirty);
      expect(clean).not.toContain('<script');
      expect(clean).not.toContain('<SCRIPT');
    }
  });

  it('外部引用按 href / xlink:href 清理（href 形态），事件属性一律清除', () => {
    const clean = sanitizeSvg(
      '<svg><use xlink:href="http://evil.example/a.svg"/>' +
        '<a href="http://evil.example/b"><text>x</text></a>' +
        '<rect onload="evil()" onclick="evil()" width="1" height="1"/></svg>',
    );
    expect(clean).not.toContain('evil.example');
    expect(clean.toLowerCase()).not.toContain('onload');
    expect(clean.toLowerCase()).not.toContain('onclick');
    expect(svgHasDangerousContent(clean)).toBe(false);
  });

  /**
   * 回归锁（v1.5.0 修复后）：
   *
   * 曾经的缺口 —— `sanitizeSvg` 是正则式清洗，只覆盖「完整的 `<script …>` 标签」
   * 与 `href` / `xlink:href` 两种外部引用形态：
   *   ① 未闭合的 `<script`（末尾被截断、没有 `>`）不会被移除；
   *   ② `<img src=...>` / `<image src=...>` 的 `src`（非 href）外部回连不会被移除。
   *
   * 两条现在都必须被清掉：裸 `src` 与 `href` 一样能让渲染进程发起网络请求，
   * 而残缺的 `<script` 一旦被拼进更大的 HTML 字符串就会被解析成真实脚本标签。
   */
  it('未闭合 script 与 src 形态的外部引用都必须被移除', () => {
    const truncated = '<svg><script src="http://evil.example/x.js"';
    const cleanedTruncated = sanitizeSvg(truncated);
    expect(cleanedTruncated).not.toContain('<script');
    expect(cleanedTruncated).not.toContain('evil.example');
    expect(svgHasDangerousContent(cleanedTruncated)).toBe(false);

    const srcForm = '<svg><img src="http://evil.example/x.png"/></svg>';
    const cleanedSrc = sanitizeSvg(srcForm);
    expect(cleanedSrc).not.toContain('http://evil.example');
    expect(cleanedSrc).not.toContain('<img');
    expect(svgHasDangerousContent(cleanedSrc)).toBe(false);

    // SVG 原生的 <image> 同样走 src / href 两种写法
    for (const form of [
      '<svg><image href="http://evil.example/a.png"/></svg>',
      '<svg><image src="http://evil.example/a.png"/></svg>',
      '<svg><image xlink:href="//evil.example/a.png"/></svg>',
    ]) {
      const clean = sanitizeSvg(form);
      expect(clean).not.toContain('evil.example');
      expect(svgHasDangerousContent(clean)).toBe(false);
    }
  });

  it('清洗不会误伤正常图形（data: 内联图片与常规元素保留）', () => {
    const ok =
      '<svg viewBox="0 0 10 10">' +
      '<rect width="10" height="10"/>' +
      '<text x="1" y="2">中文</text>' +
      '<image href="data:image/png;base64,iVBORw0KGgo="/>' +
      '<a href="#node1"><text>内部锚点</text></a>' +
      '</svg>';
    const clean = sanitizeSvg(ok);
    expect(clean).toContain('<rect');
    expect(clean).toContain('中文');
    // data: 内联图片不联网，必须保留（mermaid 的图标走这条路）
    expect(clean).toContain('data:image/png;base64');
    // 内部锚点保留
    expect(clean).toContain('#node1');
    expect(svgHasDangerousContent(clean)).toBe(false);
  });

  it('普通 Markdown 片段的 XSS 清洗覆盖 script / 事件属性 / 危险协议', () => {
    const html = renderMarkdown(
      '<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[点我](javascript:alert(1))\n\n[外链](http://ok.example)',
    );
    expect(html).not.toContain('<script');
    expect(html.toLowerCase()).not.toContain('onerror');
    expect(html).not.toContain('javascript:');
    // 正常外链保留，但必须带 noopener
    expect(html).toContain('http://ok.example');
    expect(html).toContain('rel="noopener noreferrer nofollow"');
  });
});

describe('缓存键与日志的脱敏（§9.4）', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it('缓存键是哈希，不含图表源码明文', async () => {
    const code = 'flowchart LR\n  SECRET_MARKER_9f3a --> B';
    const key = await cacheKeyOf(code, 'light');
    expect(key).not.toContain('SECRET_MARKER_9f3a');
    expect(key).not.toContain('flowchart');
    expect(key).toMatch(/^[0-9a-f]+$/);
  });

  it('缓存键随源码与主题变化（主题参与 hash → 换主题不会命中旧图）', async () => {
    const code = 'flowchart LR\n  A --> B';
    const light = await cacheKeyOf(code, 'light');
    const dark = await cacheKeyOf(code, 'dark');
    expect(light).not.toBe(dark);
    expect(await cacheKeyOf(code, 'light')).toBe(light);
    expect(await cacheKeyOf(`${code}\n  B --> C`, 'light')).not.toBe(light);
  });

  it('渲染与清洗过程不向 console 输出图表源码', async () => {
    const spyLog = vi.spyOn(console, 'log').mockImplementation(() => {});
    const spyWarn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const spyErr = vi.spyOn(console, 'error').mockImplementation(() => {});
    const spyDebug = vi.spyOn(console, 'debug').mockImplementation(() => {});

    const code = 'flowchart LR\n  LEAK_MARKER_7c1b --> B';
    await cacheKeyOf(code, 'dark');
    sanitizeSvg(EVIL_SVG);
    renderMarkdown(`\`\`\`mermaid\n${code}\n\`\`\``);

    const all = [spyLog, spyWarn, spyErr, spyDebug]
      .flatMap((spy) => spy.mock.calls)
      .map((args) => args.map(String).join(' '))
      .join('\n');
    expect(all).not.toContain('LEAK_MARKER_7c1b');

    vi.restoreAllMocks();
  });
});

describe('三端 mermaid 常量一致性（AC-9）', () => {
  it('饼图分区色 10 个且与桌面/移动端同序同值', () => {
    expect([...PIE_COLORS]).toEqual([
      '#6366f1', '#0ea5e9', '#10b981', '#f59e0b', '#f43f5e',
      '#a855f7', '#14b8a6', '#f97316', '#64748b', '#8b5cf6',
    ]);
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});
