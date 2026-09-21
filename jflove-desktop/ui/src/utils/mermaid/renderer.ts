/**
 * Mermaid 渲染调度器
 *
 * 设计文档 §4.1 / §4.7 的落地：
 *   - 渲染前先查缓存（命中则零成本返回，不触碰引擎）；
 *   - 并发闸门（默认 2）：一页 10 张图不会同时渲染，避免主线程卡顿；
 *   - 单图超时（默认 5s，首次含引擎初始化）：超时即降级，不无限等待；
 *   - 引擎不可用/加载失败 → 返回 engine 失败，由调用方展示源码卡；
 *   - 语法错误 → 解析出行号，交给错误卡展示（AC-12）；
 *   - 渲染用的临时容器挂在**离屏节点**上（不能在页面里可见地闪一下）。
 */
import { shortHash } from '../hash';
import { getCached, setCached } from './cache';
import { buildMermaidConfig } from './config';
import { loadMermaid } from './loader';
import { applyMatrix, invertMatrix, matrixOf, multiplyMatrix } from './matrix';
import { detectDiagramKind, type MermaidError, type MermaidResult, type ThemeName } from './types';

/** 引擎版本：参与缓存键；升级离线包时必须同步（见 mermaid.version.json） */
const MERMAID_VERSION = '11.16.0';

/**
 * 渲染管线版本：参与缓存键。
 *
 * 改了"渲染后处理"（尺寸计算 / 清洗顺序等）就必须 +1 —— 否则同一会话里已经缓存过的
 * 旧产物会被继续命中，让人误以为改动没生效（渲染结果只存在内存缓存里，
 * 但开发时热更新不会清空它）。
 *   1 = 尺寸归一化是死代码（量尺寸时 SVG 还没插进 DOM，永远量不到）
 *   2 = 真正生效：先插 DOM 再量，viewBox 按实测内容撑开
 */
const PIPELINE_VERSION = '2';

/**
 * 判定"故意画超长、靠视口裁掉"的辅助图形时的最小容差（viewBox 单位）。
 *
 * 实测（`设计预览/probe_overflow_elements.mjs`）：真实内容最多超出 viewBox ~80px，
 * 而辅助线超出 1800px（时序图生命线）到 28000px（甘特图今日线）——
 * 两类差了一个数量级，48px 的下限足够把极小 viewBox 也照顾到。
 */
const OVERFLOW_HELPER_MIN = 48;

/**
 * 撑开 viewBox 时留的余量（viewBox 单位）。
 * 不加余量的话，刚好落在边界上的描边会被削掉半像素（实测流程图右侧节点）。
 */
const FIT_MARGIN = 2;

/** 完全量不到尺寸时的兜底画布（正常情况下不该用到） */
const FALLBACK_W = 600;
const FALLBACK_H = 400;

const RENDER_TIMEOUT_MS = 5000;
const MAX_CONCURRENT = 2;

let activeCount = 0;
const queue: Array<() => void> = [];

/** 已初始化的主题；切换主题后需要重新 initialize */
let initializedTheme: ThemeName | null = null;

/** 渲染 ID 自增（mermaid 用 id 生成 <style>#id{}，重复 id 会串样式） */
let idSeq = 0;

/** 串行闸门：超过并发上限时排队 */
async function acquire(): Promise<void> {
  if (activeCount < MAX_CONCURRENT) {
    activeCount++;
    return;
  }
  await new Promise<void>((resolve) => queue.push(resolve));
  activeCount++;
}

function release(): void {
  activeCount--;
  const next = queue.shift();
  if (next) next();
}

/** 构造缓存键（源码 + 主题 + 引擎版本 + 渲染管线版本，任一变化即失效） */
export async function cacheKeyOf(code: string, theme: ThemeName): Promise<string> {
  return shortHash(`${code}\n${theme}\n${MERMAID_VERSION}\n${PIPELINE_VERSION}`);
}

/**
 * 渲染一张 Mermaid 图。
 *
 * @param code  mermaid 源码（属于笔记正文，**不得**写入日志）
 * @param theme 当前主题（决定 themeVariables）
 */
export async function renderMermaid(code: string, theme: ThemeName): Promise<MermaidResult> {
  const trimmed = code.trim();
  if (!trimmed) {
    return fail('parse', 0, '图表内容为空');
  }

  const key = await cacheKeyOf(trimmed, theme);
  const cached = getCached(key);
  if (cached) return cached;

  let mermaid;
  try {
    mermaid = await loadMermaid();
  } catch (e) {
    return fail('engine', 0, e instanceof Error ? e.message : '渲染引擎不可用');
  }

  // 主题变化时需要重新 initialize（themeVariables 编译进 SVG）
  if (initializedTheme !== theme) {
    try {
      mermaid.initialize(buildMermaidConfig(theme));
      initializedTheme = theme;
    } catch (e) {
      return fail('engine', 0, `渲染引擎初始化失败：${msg(e)}`);
    }
  }

  await acquire();
  const started = performance.now();
  try {
    // 渲染容器挂在离屏节点：mermaid 需要真实 DOM 来计算文本尺寸
    const holder = document.createElement('div');
    holder.setAttribute('aria-hidden', 'true');
    holder.style.cssText = 'position:absolute;left:-99999px;top:0;visibility:hidden;';
    document.body.appendChild(holder);

    try {
      // 先 parse 拿到结构化错误（render 抛错的定位信息不如 parse 稳定）
      await withTimeout(mermaid.parse(trimmed), RENDER_TIMEOUT_MS);

      const id = `jf-mmd-${++idSeq}`;
      const { svg: rawSvg } = await withTimeout(mermaid.render(id, trimmed), RENDER_TIMEOUT_MS);

      // ⚠ 必须**先把 SVG 插进 DOM** 再做后处理：量尺寸与标签转换都要真实布局。
      // v1.5.0 初版忘了这一步（只把字符串传给一个空容器去 querySelector），
      // 导致重新测量**从未生效** —— 门面看着有、实际是死代码，
      // 于是五类图的文字一直被 viewBox 裁掉。顺序错了等于没有这个安全网。
      holder.innerHTML = rawSvg;
      const svgEl = holder.querySelector('svg');

      // 尺寸按**实测内容**重算：引擎给的 viewBox 在部分图表上装不下自己画的内容
      // （SVG 默认 overflow:hidden ⇒ 文字被裁；导出用的是同一份字符串 ⇒ 下载的图片也不全）。
      // 只放不缩：本来能看见的一定还在，时序图/甘特图这类没超出的图尺寸一字不变。
      const svg = svgEl ? fitSvgToContent(svgEl) : rawSvg;
      const size = measureSvg(svg);
      const result: MermaidResult = {
        ok: true,
        svg,
        width: size.width,
        height: size.height,
        elapsed: Math.round(performance.now() - started),
        kind: detectDiagramKind(trimmed),
      };
      setCached(key, result);
      return result;
    } finally {
      holder.remove();
    }
  } catch (e) {
    if (isTimeout(e)) return fail('timeout', 0, `渲染超过 ${RENDER_TIMEOUT_MS / 1000} 秒`);
    const { line, message } = extractParseError(e);
    return fail(line > 0 ? 'parse' : 'runtime', line, message);
  } finally {
    release();
  }
}

/* ==================== 内部工具 ==================== */

function fail(reason: MermaidError['reason'], line: number, message: string): MermaidResult {
  return { ok: false, error: { reason, line, message } };
}

function msg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** 超时哨兵：用一个可识别的值区分「超时」与「渲染报错」 */
const TIMEOUT = Symbol('mermaid-timeout');

function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(TIMEOUT), ms);
    p.then(
      (v) => {
        clearTimeout(timer);
        resolve(v);
      },
      (e) => {
        clearTimeout(timer);
        reject(e);
      },
    );
  });
}

function isTimeout(e: unknown): boolean {
  return e === TIMEOUT;
}

/**
 * 从 mermaid 的错误对象里提取行号与原因。
 *
 * mermaid 的解析错误通常是 `Error`，带 `hash.loc.first_line`（0-based）。
 * 提取失败时返回 line=0（前端展示"无法定位行号"，但原因与源码照常给出）。
 */
function extractParseError(e: unknown): { line: number; message: string } {
  const err = e as { hash?: { loc?: { first_line?: number } }; message?: string };
  const first = err?.hash?.loc?.first_line;
  const line = typeof first === 'number' && Number.isFinite(first) ? first + 1 : 0;
  // 只取第一行，避免把整段 mermaid 内部栈信息暴露给用户
  const raw = (err?.message ?? String(e)).split('\n')[0].trim();
  const message = raw.slice(0, 200) || '图表语法错误';
  return { line, message };
}

/** 从 SVG 字符串里读出宽高（优先 viewBox，其次 width/height 数值） */
function measureSvg(svg: string): { width: number; height: number } {
  const vb = /viewBox="([-\d.]+)\s+([-\d.]+)\s+([\d.]+)\s+([\d.]+)"/.exec(svg);
  if (vb) {
    const w = Number(vb[3]);
    const h = Number(vb[4]);
    if (Number.isFinite(w) && Number.isFinite(h) && w > 0 && h > 0) {
      return { width: Math.ceil(w), height: Math.ceil(h) };
    }
  }
  const w = /width="(\d+(?:\.\d+)?)"/.exec(svg);
  const h = /height="(\d+(?:\.\d+)?)"/.exec(svg);
  return {
    width: w ? Math.ceil(Number(w[1])) : 600,
    height: h ? Math.ceil(Number(h[1])) : 400,
  };
}

/**
 * 按**实测内容**重算 SVG 尺寸（v1.5.0 反馈修复 · 第二轮）。
 *
 * ## 为什么必须做（实测，不是推断）
 *
 * mermaid 11.16.0 给的 `viewBox` 两头都不可信：
 *   - `htmlLabels: false` 时它**自己量 SVG 文字，量得比画的窄** ⇒ viewBox 装不下内容，
 *     SVG 默认 `overflow: hidden` 直接把内容裁掉。实测 7 类图 **5 类文字被裁**
 *     （流程图右裁 50px、饼图上裁 2px、类图下裁 26.5px、ER 下裁 40px… 见
 *     `设计预览/find_clipped_text.mjs`）。用户说的「显示不全」就是它；
 *     而**导出/下载的 SVG 是同一份字符串**，所以下载的图片同样不全。
 *   - `htmlLabels: true`（当前采用，理由见 `config.ts`）排版正确，但引擎算出的
 *     viewBox 会**膨胀约 10 倍**（实测 class 2788×2788、ER 2736×2070），
 *     图会被缩成一小块、四周全是空白。
 *
 * 所以这里按实测内容**重算**：既会放大（防裁切）也会收缩（去空白）。
 *
 * ## 为什么不能无脑取"所有内容的并集"
 *
 * 有些图形是 mermaid **故意画超长、靠视口裁掉**的辅助线：
 *   - 时序图 `line.actor-line` 画到 y=2000（视口 185）；
 *   - 甘特图 `line.today` 落在 x=28399（视口 1374）。
 * 若把它们并进来，图会被撑成一大片空白。好在两类东西**量级差得很远**
 * （实测：真实内容最多超 ~80px，辅助线超 1800~28000px），判据因此很稳：
 *   - **所有非空 `<text>` 一律计入**（文字永远不是"故意画超长"的东西，绝不能裁）；
 *   - 图形只有在**每个方向**的超出量都不超过「该方向 viewBox 尺寸」时才计入
 *     （下限 48px，兼容极小 viewBox）。
 *
 * ## 为什么是"只放不缩"
 *
 * 引擎的 viewBox 只在"装不下自己画的内容"时才有问题；一旦内容真的超出去，就把它撑到
 * 刚好装下（并在超出的一侧留 2px 余量，免得描边被削）。**绝不收缩**：
 *   - 本来能看见的东西一定还在，不会引入"改完反而看不见"的新故障面；
 *   - 时序图 / 甘特图 / 饼图的尺寸与修复前完全一致（它们本来就没超），
 *     前后对照仍然有意义。
 *
 * @param el 已经挂在文档里的 `<svg>` 元素（**必须真的在 DOM 里**，否则量不到）
 * @returns  序列化后的 SVG 字符串；量不到时也保证返回**合法可渲染**的 SVG
 */
function fitSvgToContent(el: SVGSVGElement | null): string {
  if (!el) return '';

  const rawVb = (el.getAttribute('viewBox') || '').trim().split(/[\s,]+/).map(Number);
  let ox = Number.isFinite(rawVb[0]) ? rawVb[0] : 0;
  let oy = Number.isFinite(rawVb[1]) ? rawVb[1] : 0;
  let vw = Number.isFinite(rawVb[2]) && rawVb[2] > 0 ? rawVb[2] : 0;
  let vh = Number.isFinite(rawVb[3]) && rawVb[3] > 0 ? rawVb[3] : 0;
  const brokenBox = vw <= 0 || vh <= 0;

  // 量测本身绝不允许把渲染搞挂：引擎已经渲染成功了，最坏情况是尺寸没修正
  // （图形仍可见，只是可能被裁），不能因为量测异常让整张图变成错误卡。
  let bounds: { x1: number; y1: number; x2: number; y2: number } | null = null;
  try {
    bounds = measureContentBounds(el, { ox, oy, vw, vh });
  } catch {
    bounds = null;
  }

  if (bounds && brokenBox) {
    // viewBox 损坏（引擎在无布局容器里量到 0）→ 直接用实测内容范围，
    // 否则图会整张不可见（桌面端甘特图踩过 viewBox="0 0 0 144"）
    ox = bounds.x1 - FIT_MARGIN;
    oy = bounds.y1 - FIT_MARGIN;
    vw = bounds.x2 - bounds.x1 + FIT_MARGIN * 2;
    vh = bounds.y2 - bounds.y1 + FIT_MARGIN * 2;
  } else if (bounds) {
    let bx1 = ox;
    let by1 = oy;
    let bx2 = ox + vw;
    let by2 = oy + vh;
    if (bounds.x1 < bx1) bx1 = bounds.x1 - FIT_MARGIN;
    if (bounds.y1 < by1) by1 = bounds.y1 - FIT_MARGIN;
    if (bounds.x2 > bx2) bx2 = bounds.x2 + FIT_MARGIN;
    if (bounds.y2 > by2) by2 = bounds.y2 + FIT_MARGIN;
    ox = bx1;
    oy = by1;
    vw = bx2 - bx1;
    vh = by2 - by1;
  }

  if (!Number.isFinite(vw) || vw <= 0) vw = FALLBACK_W;
  if (!Number.isFinite(vh) || vh <= 0) vh = FALLBACK_H;

  const round = (v: number) => Math.round(v * 100) / 100;
  el.setAttribute('viewBox', `${round(ox)} ${round(oy)} ${Math.ceil(vw)} ${Math.ceil(vh)}`);
  // mermaid 写的是 width="100%"，必须换成具体像素：否则窄图会被横向拉变形
  // （见 components.css 的尺寸策略说明）
  el.setAttribute('width', String(Math.ceil(vw)));
  el.setAttribute('height', String(Math.ceil(vh)));
  // 删掉内联 max-width（mermaid 量失败时会写 max-width:0px，会把图压没）
  const keptStyle = (el.getAttribute('style') || '')
    .split(';')
    .filter((part) => part.trim() && !/^\s*max-width\s*:/.test(part))
    .join(';');
  if (keptStyle) el.setAttribute('style', keptStyle);
  else el.removeAttribute('style');

  return serializeSvg(el);
}

/**
 * 把 `<svg>` 元素序列化成字符串。
 *
 * ⚠ 不能直接读 `outerHTML`：HTML 序列化会改写自闭合标签与命名空间写法，
 * 而我们要的是**能被独立打开的 SVG 文件**（导出按钮写盘的就是这个字符串）。
 * `XMLSerializer` 会带上根元素的 `xmlns`，导出后单独打开也正常。
 */
function serializeSvg(el: SVGSVGElement): string {
  try {
    const xml = new XMLSerializer().serializeToString(el);
    // 少数实现会漏掉 xmlns，导出成独立文件时必需
    return /^<svg[^>]*\sxmlns=/.test(xml)
      ? xml
      : xml.replace(/^<svg\b/, '<svg xmlns="http://www.w3.org/2000/svg"');
  } catch {
    return el.outerHTML;
  }
}

/**
 * 量出"必须装进 viewBox"的内容范围（单位：viewBox 用户单位）。
 *
 * 换算方式：`element.getCTM()` 得到"元素用户空间 → **视口**"的矩阵，
 * 而 `<svg>` 自身的 `getCTM()` 同样含 viewBox 变换，因此
 * `svg.getCTM().inverse() × element.getCTM()` 才是"元素坐标 → svg 用户空间"。
 * 实测与"按视口像素 × 缩放比"两条独立路径的结果**一致到 0.03px**
 * （`设计预览/probe_coord_method.mjs`），两种写法都可靠；这里用矩阵法，
 * 因为它不依赖"preserveAspectRatio 是等比"这个前提。
 */
function measureContentBounds(
  el: SVGSVGElement,
  vb: { ox: number; oy: number; vw: number; vh: number },
): { x1: number; y1: number; x2: number; y2: number } | null {
  let x1 = Infinity;
  let y1 = Infinity;
  let x2 = -Infinity;
  let y2 = -Infinity;
  let touched = false;

  const svgCTM = matrixOf(el);
  const toUser = svgCTM ? invertMatrix(svgCTM) : null;

  /** 把元素自己的用户空间矩形并进来 */
  const include = (node: SVGGraphicsElement) => {
    let box: DOMRect;
    try {
      box = node.getBBox();
    } catch {
      return; // 未渲染/无几何的元素（如 display:none）会抛错，跳过
    }
    if (!(box.width > 0 || box.height > 0)) return;
    // 元素自身到 svg 用户空间的矩阵
    const own = matrixOf(node);
    const mat = own ? (toUser ? multiplyMatrix(toUser, own) : own) : null;
    if (!mat) return;
    const corners: Array<[number, number]> = [
      [box.x, box.y],
      [box.x + box.width, box.y],
      [box.x, box.y + box.height],
      [box.x + box.width, box.y + box.height],
    ];
    touched = true;
    for (const [px, py] of corners) {
      const p = applyMatrix(mat, px, py);
      if (!Number.isFinite(p.x) || !Number.isFinite(p.y)) continue;
      x1 = Math.min(x1, p.x);
      y1 = Math.min(y1, p.y);
      x2 = Math.max(x2, p.x);
      y2 = Math.max(y2, p.y);
    }
  };

  // 所有非空文字一律计入 —— 文字被裁是最不可接受的（就是用户说的"显示不全"）
  el.querySelectorAll('text').forEach((t) => {
    if (!(t.textContent ?? '').trim()) return;
    include(t);
  });

  // 图形：排除"故意画超长、靠视口裁掉"的辅助线（见 fitSvgToContent 注释）
  const shapeLimitX = Math.max(vb.vw, OVERFLOW_HELPER_MIN);
  const shapeLimitY = Math.max(vb.vh, OVERFLOW_HELPER_MIN);
  el.querySelectorAll('rect, path, polygon, circle, ellipse, line').forEach((n) => {
    if (n.closest('defs, marker, clipPath, mask, symbol')) return; // 定义块不参与绘制
    const node = n as SVGGraphicsElement;
    let box: DOMRect;
    try {
      box = node.getBBox();
    } catch {
      return;
    }
    // 先用元素自己的用户空间粗判超界量（此时还没换到 svg 空间，够用于筛掉巨物）
    const bigX = Math.max(vb.ox - box.x, box.x + box.width - (vb.ox + vb.vw));
    const bigY = Math.max(vb.oy - box.y, box.y + box.height - (vb.oy + vb.vh));
    if (bigX > shapeLimitX || bigY > shapeLimitY) return; // 辅助线，不计入
    include(node);
  });

  if (!touched || !Number.isFinite(x1)) return null;
  return { x1, y1, x2, y2 };
}

/** 供测试重置模块级状态 */
export const __testing = {
  reset() {
    activeCount = 0;
    queue.length = 0;
    initializedTheme = null;
    idSeq = 0;
  },
  measureSvg,
  fitSvgToContent,
  measureContentBounds,
  serializeSvg,
  extractParseError,
  OVERFLOW_HELPER_MIN,
  FIT_MARGIN,
  PIPELINE_VERSION,
  RENDER_TIMEOUT_MS,
  MAX_CONCURRENT,
};
