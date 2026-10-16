/**
 * 「登录后的落点」**唯一真相叶子模块**单测 —— v1.6.1 Phase 6 收尾轮（缺陷 R2 / R4 · AC-64 ②）
 *
 * ## 判据出处
 *
 * - 缺陷 **R2**（本轮用户裁决"本版清掉这条债"）：消除
 *   `LoginPage → editor-open-paths → config/routes → LoginPage` 的**模块环**；
 * - 缺陷 **R4**：`/editor` 字面量曾有私有副本（`utils/scratch/shortcut-runtime.ts:52`），
 *   现统一取 `EDITOR_ROUTE`；
 * - 需求 `文档记录/需求文档/v1.6.1.md:469`（AC-64 ②：未登录唤起 → 完成登录 ⇒ 左侧菜单自动切到编辑器）；
 * - 设计 `文档记录/设计文档/v1.6.1.md` §4.4（唤起 → 登录门 → 打开并揭示）。
 *
 * ## 用例分工
 *
 * | # | 判据 | 失效表现 |
 * |---|------|----------|
 * | ① | 落点判据三态：默认假 → `mark` 真 → `clear` 假 | 默认真 ⇒ 每次普通登录都被带到编辑器；不清 ⇒ 上一次唤起污染下一次登录 |
 * | ② | **静态扫描**：`editor-landing.ts` 源码（去注释后）**不含任何依赖边** | 叶子一旦有了依赖，模块环随时能重新长回来（缺陷 R2 回归） |
 * | ③ | **静态扫描**：`LoginPage.tsx` 不再从 `editor-open-paths` 取判据，改从叶子取 | 环的第一条边回来了 |
 * | ④ | 环的三条边逐条核对（叶子无出边 / LoginPage 无回边 / 接线侧仍指向叶子） | 有人"顺手"把边接回去，而 ②③ 恰好被改宽 |
 *
 * ## 执行方式（为什么读源码而不是 mock）
 *
 * 模块环是**静态图**上的性质，运行期观测不到（回修前真机跑通、环照样在）。
 * 唯一可判定的形态就是"读源码文本 + 逐行词法判定"，因此用例 ②③④ 用
 * `node:fs` + `import.meta.url` 直读仓库内源码（`node:*` 的最小类型声明见 `src/test/node-builtins.d.ts`）。
 * 注释里出现"import"字样是正常的中文说明，故扫描前**先剥离块注释与行注释**，避免自命中。
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  EDITOR_ROUTE,
  clearEditorRevealOnLogin,
  editorRevealPendingOnLogin,
  markEditorRevealOnLogin,
} from '../editor-landing';

/** 读同仓库源码文本（用 `import.meta.url` 定位，不依赖 `process.cwd()`） */
function readSource(relativeToThisFile: string): string {
  return readFileSync(fileURLToPath(new URL(relativeToThisFile, import.meta.url)), 'utf8');
}

/**
 * 剥离块注释（含 JSDoc）与**整行**行注释 —— 静态扫描只看代码，不让中文说明里的
 * "import" 字样自命中（本仓库源码里的行注释都是整行形态，见既有风格）。
 */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*$/gm, '');
}

/** 注释剥离后仍存在的依赖边（`import` / `import(` / `require(` / `export … from`） */
function dependencyEdgesOf(source: string): string[] {
  return stripComments(source)
    .split('\n')
    .filter(
      (line) =>
        /^\s*import\b/.test(line) ||
        /\bimport\s*\(/.test(line) ||
        /\brequire\s*\(/.test(line) ||
        /^\s*export\b[^\n]*\bfrom\b/.test(line),
    );
}

/** 被测叶子模块路径（本文件在 `src/utils/scratch/__tests__/`） */
const LEAF_SOURCE = '../editor-landing.ts';
/** 环的第一个节点（消费判据的页面） */
const LOGIN_PAGE_SOURCE = '../../../pages/LoginPage.tsx';
/** 环的第二个节点（接线模块，仍 import `config/routes`） */
const OPEN_PATHS_SOURCE = '../editor-open-paths.ts';

beforeEach(() => {
  // 判据是模块级标记：用例之间显式复位，保证每条用例的起点都是"没有待揭示的落点"
  clearEditorRevealOnLogin();
});

describe('① 落点判据的三态（默认假 / 置真 / 复位）', () => {
  it('①全新加载的模块实例判据为假，且路由常量就是 /editor（唯一真相）', async () => {
    // 用 `vi.resetModules()` + 动态 import 拿一个**全新实例**，才谈得上"默认值"判据
    // （静态 import 的实例可能已被别的用例 mark 过）
    vi.resetModules();
    const fresh = await import('../editor-landing');

    expect(
      fresh.editorRevealPendingOnLogin(),
      '默认必须为假 —— 否则普通登录（无外部唤起）会被带到编辑器，AC-64 ② 的反面「永远去编辑器」',
    ).toBe(false);
    expect(fresh.EDITOR_ROUTE, '编辑器工作区路由常量（AC-64 ② 的观测点）').toBe('/editor');
  });

  it('①markEditorRevealOnLogin() ⇒ 判据为真；clearEditorRevealOnLogin() ⇒ 复位为假（可重复）', () => {
    // 静态导入实例的常量（与动态导入实例同源，都是本叶子模块的导出）
    expect(EDITOR_ROUTE, '落点常量与 `config/routes.tsx` 登记的编辑器路由一致').toBe('/editor');
    expect(editorRevealPendingOnLogin()).toBe(false);

    markEditorRevealOnLogin();
    expect(
      editorRevealPendingOnLogin(),
      '置位后 LoginPage 的落点 effect 必须读到 /editor 的判据',
    ).toBe(true);

    clearEditorRevealOnLogin();
    expect(
      editorRevealPendingOnLogin(),
      '复位后必须回到 /（登出 ⇒ 不允许上一次唤起污染下一次普通登录）',
    ).toBe(false);

    // 三态可反复切换（幂等，不累积状态）
    markEditorRevealOnLogin();
    markEditorRevealOnLogin();
    expect(editorRevealPendingOnLogin(), '重复置位仍是真，不产生副作用').toBe(true);
    clearEditorRevealOnLogin();
    clearEditorRevealOnLogin();
    expect(editorRevealPendingOnLogin(), '重复复位仍是假').toBe(false);
  });
});

describe('② 防环回归（叶子模块零依赖）', () => {
  it('②editor-landing.ts 源码（去注释后）不含任何 import / require / re-export 依赖边', () => {
    const edges = dependencyEdgesOf(readSource(LEAF_SOURCE));

    expect(
      edges,
      '叶子模块一旦有出边，`LoginPage → … → config/routes → LoginPage` 的模块环就能重新长回来（缺陷 R2）',
    ).toEqual([]);

    // 逐行词法判定的补充形态：整行以 `import` 开头的行必须一条都没有
    const importLines = stripComments(readSource(LEAF_SOURCE))
      .split('\n')
      .filter((line) => /^\s*import\b/.test(line));
    expect(importLines).toEqual([]);
  });

  it('②扫描器本身可靠：喂一份"有依赖边"的样本必须被认出来（防用例永远为绿）', () => {
    const sample = [
      '/** 说明里的 import 不算边 */',
      "import { router } from '../../config/routes';",
      "const lazy = () => import('./x');",
      "const cjs = require('./y');",
      "export { z } from './z';",
      '// import 在行注释里也不算',
      'const text = "import 出现在字符串里" + 1;',
    ].join('\n');

    // 4 行是真实依赖边（静态 import / 动态 import / require / re-export），注释与字符串不是
    expect(dependencyEdgesOf(sample)).toHaveLength(4);
    expect(dependencyEdgesOf('// import a from "b";\n/** import c from "d"; */')).toEqual([]);
  });
});

describe('③ 防环回归（LoginPage 的回边已改向叶子模块）', () => {
  it('③LoginPage.tsx 不再 import `../utils/scratch/editor-open-paths`，改取 `editor-landing`', () => {
    // 只看代码、不看注释：注释里提到模块路径是正常的中文说明
    const source = stripComments(readSource(LOGIN_PAGE_SOURCE));

    expect(
      source,
      '环的第一条边（LoginPage → editor-open-paths）必须消失（缺陷 R2）',
    ).not.toContain("from '../utils/scratch/editor-open-paths'");
    expect(
      source,
      '落点判据与路由常量必须改从零依赖叶子模块取',
    ).toContain("from '../utils/scratch/editor-landing'");
  });

  it('③环的三条边逐条核对：接线侧仍指向叶子，且叶子不指回任何页面', () => {
    const openPaths = stripComments(readSource(OPEN_PATHS_SOURCE));

    // 边 2（既有边，必须保留）：接线模块仍 import `config/routes`（它不是环的成因）
    expect(openPaths, '`config/routes` 的 import 保持不变（环只由 LoginPage 指回来造成）').toContain(
      "from '../../config/routes'",
    );
    // 边 3（新边，指向叶子）：判据与路由常量来自零依赖叶子
    expect(openPaths, '判据改从叶子模块取').toContain("from './editor-landing'");
    expect(openPaths, '接线模块不得 import 页面（否则环立刻成立）').not.toMatch(
      /from\s+'[^']*pages\//,
    );

    // 叶子侧：既不看路由、也不看页面、也不看接线模块
    const leaf = stripComments(readSource(LEAF_SOURCE));
    for (const forbidden of ['config/routes', 'pages/LoginPage', './editor-open-paths']) {
      expect(leaf, `叶子模块不得依赖 ${forbidden}`).not.toContain(forbidden);
    }
  });
});
