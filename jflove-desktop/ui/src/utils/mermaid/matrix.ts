/**
 * SVG 2D 仿射矩阵工具（自实现，不依赖 `DOMMatrix`/`DOMPoint`）
 *
 * 为什么自己实现：
 *   1. 我们只需要 2×3 仿射矩阵的求逆/相乘，浏览器 API 在旧 WebView 上并非都有；
 *   2. jsdom（单测环境）**完全没有** `DOMMatrix` / `DOMPoint`，用它们会逼得单测只能跳过；
 *   3. 矩阵运算必须能在"量尺寸"这条关键路径上被单测覆盖 —— 量错了会直接把图裁掉。
 *
 * 坐标系约定（这是整条链路的关键）：
 *   `element.getCTM()` 返回"该元素的用户空间 → **视口**"的矩阵，
 *   而 `<svg>` 自身的 `getCTM()` **同样包含 viewBox 变换**。
 *   因此 `svg.getCTM().inverse() × element.getCTM()` 才能把元素坐标换算到
 *   **svg 用户空间**（也就是 viewBox 用的那套单位）。
 */

/** 2D 仿射矩阵（与 SVG matrix(a,b,c,d,e,f) 同序） */
export interface Matrix2D {
  a: number;
  b: number;
  c: number;
  d: number;
  e: number;
  f: number;
}

/** 单位矩阵 */
export const IDENTITY_MATRIX: Matrix2D = { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 };

/**
 * 取元素的 CTM；不存在或含非法值时返回 null。
 *
 * 入参故意用 `unknown`：DOM 里能取 CTM 的是 `SVGGraphicsElement` 等少数类型，
 * 而调用点拿到的是 `Element`（`querySelector` 的静态类型），用结构化类型会逼调用方
 * 到处写断言。这里内部做能力探测。
 */
export function matrixOf(node: unknown): Matrix2D | null {
  try {
    const getter = (node as { getCTM?: () => unknown } | null | undefined)?.getCTM;
    if (typeof getter !== 'function') return null;
    const m = getter.call(node) as Matrix2D | null | undefined;
    if (!m) return null;
    if ([m.a, m.b, m.c, m.d, m.e, m.f].some((v) => !Number.isFinite(v))) return null;
    return { a: m.a, b: m.b, c: m.c, d: m.d, e: m.e, f: m.f };
  } catch {
    return null;
  }
}

/** 求逆；不可逆时返回 null */
export function invertMatrix(m: Matrix2D): Matrix2D | null {
  const det = m.a * m.d - m.b * m.c;
  if (!det || !Number.isFinite(det)) return null;
  return {
    a: m.d / det,
    b: -m.b / det,
    c: -m.c / det,
    d: m.a / det,
    e: (m.c * m.f - m.d * m.e) / det,
    f: (m.b * m.e - m.a * m.f) / det,
  };
}

/** 矩阵相乘（先 left 后 right，与 SVG 嵌套变换语义一致） */
export function multiplyMatrix(left: Matrix2D, right: Matrix2D): Matrix2D {
  return {
    a: left.a * right.a + left.c * right.b,
    b: left.b * right.a + left.d * right.b,
    c: left.a * right.c + left.c * right.d,
    d: left.b * right.c + left.d * right.d,
    e: left.a * right.e + left.c * right.f + left.e,
    f: left.b * right.e + left.d * right.f + left.f,
  };
}

/** 变换一个点 */
export function applyMatrix(m: Matrix2D, x: number, y: number): { x: number; y: number } {
  return { x: m.a * x + m.c * y + m.e, y: m.b * x + m.d * y + m.f };
}
