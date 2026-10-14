/**
 * 离线 CM6 内核（`public/vendor/editor.bundle.js`）的**测试期注入器** —— Phase 6（v1.6.0）
 *
 * ## 为什么用 `node:fs` + `new Function`，而不是应用自己的 `loadEditorBundle()`
 *
 * 应用源码的加载器（`utils/scratch/load-editor-bundle.ts:58-87`）走的是
 * 「往 `document.head` 插 `<script src="./vendor/editor.bundle.js">` + 等 `load` 事件」。
 * 但 **jsdom 默认不加载外部资源**（`resources` 不是 `usable`），该标签会永远停在 pending，
 * 用例只会**挂死**而不是失败；相对路径在 jsdom 里也没有 `ui/public/` 这个基准。
 *
 * 所以测试期**绕开加载器**，改由 Node 直接读文件字节，再用
 * `new Function('window', code)(window)` 把 IIFE 在**受控的 `window` 形参**上求值：
 *
 *   - 该文件是单个 IIFE，末尾才 `window.__JFLOVE_CM__ = PM; window.cmModules = q_;`；
 *   - 形参 `window` 遮蔽全局 `window`，注入目标因此**只可能是**这个 jsdom window，
 *     **不会**污染 Node 的全局对象；
 *   - AC-32 要求「经 `window.__JFLOVE_CM__.formatters` 真调用」，这条路径取到的正是真实现
 *     （`sql-formatter@15.9.0` / `xml-formatter@3.7.0`，版本见 bundle 内的 `version` 字段），
 *     **不是替身**；这也是 `src/test/setup.ts` 刻意不替身化 `window.__JFLOVE_CM__` 的原因。
 *
 * ## 版本自检
 *
 * `editor.version.json` 与 `src/types/codemirror-bundle.d.ts` 是**一组**（见该文件头「同步义务」）。
 * 这里顺手校验注入结果里 `formatters.sql` / `formatters.xml` 都是函数：
 * 离线包被换掉或缺文件时，用例会以明确的错误失败，而不是含糊的
 * 「Cannot read properties of undefined（reading 'sql'）」。
 */
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { JFLoveCmApi, JFLoveCmFormatters } from '../types/codemirror-bundle';

/** 本文件所在目录（`<ui>/src/test`）—— 用 `import.meta.url` 而不是 `process.cwd()`，避免依赖启动目录 */
const HERE = dirname(fileURLToPath(import.meta.url));

/** 离线包绝对路径：`<ui>/src/test/` → `<ui>/public/vendor/editor.bundle.js` */
export const CM_BUNDLE_PATH = resolve(HERE, '../../public/vendor/editor.bundle.js');

/**
 * 注入离线包并取回内核；同一测试文件内重复调用只求值一次
 * （`window.__JFLOVE_CM__` 已挂载即直接复用，语义与加载器的幂等一致）。
 */
export function loadCmBundle(): JFLoveCmApi {
  const mounted = window.__JFLOVE_CM__;
  if (mounted) return mounted;

  const code = readFileSync(CM_BUNDLE_PATH, 'utf8');
  // 离线包是**构建期产出的可信资产**（sha256 记录在 editor.version.json），
  // 这里不是求值任何用户输入；用 new Function 而不是 eval，是为了把 window 作为形参显式传进去。
  // 注：本仓库的 eslint 配置（`eslint.config.ts`）**没有**开启 no-implied-eval /
  // @typescript-eslint/no-implied-eval，所以这一行不需要 disable 指令。
  new Function('window', code)(window);

  const api = window.__JFLOVE_CM__;
  if (!api) throw new Error('离线包求值后仍未挂载 window.__JFLOVE_CM__');
  return api;
}

/** 取 `formatters`（sql / xml 的真实现）并做最小自检 */
export function loadCmFormatters(): JFLoveCmFormatters {
  const formatters = loadCmBundle().formatters;
  if (typeof formatters?.sql !== 'function' || typeof formatters?.xml !== 'function') {
    throw new Error('离线包的 formatters 缺少 sql / xml 函数（离线包与手写类型可能不同步）');
  }
  return formatters;
}
