/**
 * 编辑面板工具栏：**显示名（AC-74⑦ / ⑥）+ 工具箱关闭行为（AC-76）** 用例
 *
 * ## 判据出处
 *
 * - 需求 `文档记录/需求文档/v1.6.1.md` **AC-74**（⑦ 语言名 / ⑥ SQL 方言下拉显示名）、
 *   **AC-76**（① 10 项选完即关 / ② `sql`+`font` 保持打开 / ③ `Esc` / ④ 点外部 / ⑤ 关框不改正文）；
 * - 设计 `文档记录/设计文档/v1.6.1.md` **§15.2.5**（`languageLabel` 按 ext 优先）、
 *   **§15.2.7**（`sqlDialectLabel`）、**§15.4**（`MENU_STAYS_OPEN` + 容器委托 + Esc/点外部）。
 *
 * ## 为什么"失败得起来"
 *
 * - 语言名：分别渲染 `.txt` / `.csv` / `.tsv` / `.json` 四个标签，逐项读**下拉选中项**的可见文本。
 *   改造前 `languageLabel(language)` 只看 `language`（三者同为 `text`）⇒ 三项都是「CSV」、用例必红。
 * - 方言：断言**每个 `<option>` 的可见文本**，并用真离线包的 `sqlDialects` 做"全集遍历"
 *   ⇒ 少一项映射 / 裸回退成 id 都会红。
 * - 关闭行为：逐项点按钮后读 `aria-expanded`（`false` = 弹框已关），并对 `sql` / `font` 断言**仍为 true**；
 *   `Esc` / 点外部 / `target.disabled` / 点 `<input>` 各一条。
 *
 * 真实实现口径（证据：`components/scratch/ScratchToolbar.tsx`）
 *   - 弹框开关是局部 `useState`，开关按钮是**开关**（`setMenuOpen((open) => !open)`）⇒ 先看 `aria-expanded`；
 *   - 关闭委托挂在弹框容器的 `onPointerUpCapture`，归属由 `<li data-toolbox-item>` 表达；
 *   - `Esc` 走 `window` 捕获阶段；点外部走 `document` 的 `pointerdown` 捕获（弹框内 / 开关上不算外部）。
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, screen } from '@testing-library/react';
import { createElement } from 'react';

// ---- 依赖替身（vi.mock 会被 hoist，工厂内动态 import 避免与 helper 形成加载环） ----
vi.mock('../ScratchEditorHost', async (importOriginal) => {
  const real = await importOriginal<Record<string, unknown>>();
  const helper = await import('./scratch-editor-host-mock');
  return helper.scratchEditorHostModuleMock(real);
});

vi.mock('../../../utils/desktop-bridge', () => ({
  callBridge: vi.fn(async () => ({ ok: true })),
  isBridgeReady: () => true,
  onBridgeEvent: () => () => undefined,
  getChannelObject: () => null,
  callShell: vi.fn(async () => ({ ok: true })),
}));

vi.mock('../../../services/scratch-service', () => ({
  scratchService: {
    saveDraft: vi.fn(async () => ({ ok: true })),
    readText: vi.fn(async () => ({ ok: true, text: '' })),
    readTextOnly: vi.fn(async () => ({ ok: true, text: '' })),
    pickReadOnlyFile: vi.fn(async () => null),
    writeText: vi.fn(async () => ({ ok: true })),
    reportDirty: vi.fn(),
  },
  isEditorBridgeReady: () => true,
  toDraftPaneMeta: (tab: unknown) => tab,
}));

import { EditorPanel } from '../EditorPanel';
import { useScratchStore } from '../../../stores/scratch-store';
import type { ScratchTab } from '../../../types/scratch';
import { loadCmBundle } from '../../../test/cm-bundle';
import {
  getScratchEditorSession,
  mockSessionOf,
  registerMockSession,
  renderTabInStore,
  resetMockEditorSessions,
  resetScratchStore,
  tabTextOf,
  type TabRenderHandle,
} from './scratch-editor-host-mock';

// ---- 夹具 ------------------------------------------------------------------------

/**
 * 给替身会话补上"离线包里的格式化器"（JSON / XML / SQL 三项动作的真实依赖）。
 *
 * 为什么需要：替身会话的 `api` 只有 `{ name, formatters: { sqlDialects } }`
 * （见 `scratch-editor-host-mock.tsx` 的注释："只留测试需要的探针"）。于是
 * `formatJson` / `xmlSafeMinify` / `runSqlFormat` 会因 `session.api.formatters.json` 为
 * `undefined` 而抛错、走 `setMessage`，**动作不生效** ⇒ "结果已应用"那半边判据永远是假。
 *
 * 这里**不重写**替身的实现，只往它的 api 上补**真离线包**的格式化器（与生产同源），
 * 并让 `openSearchPanel()` 返回 `true`（真实现"面板能打开"的等价信号）。
 */
function enableRealFormatters(tabId: string): void {
  const session = mockSessionOf(tabId);
  const formatters = loadCmBundle().formatters as unknown as Record<string, unknown>;
  (session.api as unknown as { formatters: Record<string, unknown> }).formatters = {
    ...formatters,
    sqlDialects: loadCmBundle().formatters.sqlDialects,
  };
  session.openSearchPanel = () => true;
}

/** 渲染一个真实挂载的编辑面板（与 `scratch-editor-panel-font-view.test.tsx` 同一手法） */
function renderEditorTab(init: Partial<ScratchTab> = {}, text = 'a\nb\n'): TabRenderHandle {
  let knownTabId = '';
  const handle = renderTabInStore(
    () => {
      if (!getScratchEditorSession(knownTabId)) {
        registerMockSession(knownTabId, text);
        enableRealFormatters(knownTabId);
      }
      return createElement(EditorPanel, { tabId: knownTabId, active: true });
    },
    { language: 'text', detected: 'text', mounted: true, ...init },
  );
  knownTabId = handle.tabId;
  handle.sync();
  // 标签 id 只在 `renderTabInStore` 里生成 ⇒ 建好后再补一次（幂等）
  enableRealFormatters(knownTabId);
  handle.sync();
  return handle;
}

/** 取「工具箱」开关按钮（`aria-expanded` = 弹框是否可见的判据） */
function toolboxToggle(): HTMLButtonElement {
  return screen.getByRole('button', { name: /工具箱/ }) as HTMLButtonElement;
}

/** 确保弹框处于打开态 */
function openToolbox(): HTMLButtonElement {
  const toggle = toolboxToggle();
  if (toggle.getAttribute('aria-expanded') !== 'true') fireEvent.click(toggle);
  expect(toggle.getAttribute('aria-expanded'), '弹框应已打开').toBe('true');
  return toggle;
}

/** 工具箱项（按 `data-toolbox-item` 归属，与实现的委托口径同源） */
function toolboxItem(container: HTMLElement, itemId: string): HTMLElement {
  const item = container.querySelector<HTMLElement>(`[data-toolbox-item="${itemId}"]`);
  if (!item) throw new Error(`工具箱里找不到项 ${itemId}`);
  return item;
}

/** 在给定工具箱项里按文案找按钮 */
function buttonOf(item: HTMLElement, label: string | RegExp): HTMLButtonElement {
  const found = Array.from(item.querySelectorAll('button')).find((button) =>
    typeof label === 'string' ? button.textContent === label : label.test(button.textContent ?? ''),
  );
  if (!found) throw new Error(`项里找不到按钮：${String(label)}`);
  return found;
}

/**
 * 模拟真实浏览器里的"按下 → 抬起 → 点击"三联事件。
 *
 * ⚠ **必须三条都发**，两个原因（已实测，不是猜测）：
 *   1. 实现的关闭委托挂在 `pointerup` **捕获阶段**（设计 §15.4.1：用 pointerup 而不是
 *      pointerdown，避免关框时"这次点击"落在已被移除的 DOM 上）⇒ 不发 pointerup 就测不到关闭；
 *   2. **jsdom 不会由 pointer 事件合成 click**（实测：`fireEvent.pointerUp` 后按钮的 `onClick`
 *      计数为 0）⇒ 必须显式补 `click`，否则"动作真的执行了"这半边判据永远不成立
 *      （那会让用例变成"只测了关框"，AC-76① 后半句失去区分度）。
 */
function pressButton(button: HTMLButtonElement): void {
  fireEvent.pointerDown(button);
  fireEvent.pointerUp(button);
  fireEvent.click(button);
}

beforeEach(() => {
  resetMockEditorSessions();
  resetScratchStore();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ---- AC-74⑦ 语言名（工具栏下拉的可见文本） -----------------------------------------

describe('AC-74⑦ 工具栏语言下拉的显示名（按扩展名，不再一律「CSV」）', () => {
  /** 读 `<select>` 上**选中项**的可见文本 */
  function selectedLanguageText(container: HTMLElement): string {
    const selects = Array.from(container.querySelectorAll<HTMLSelectElement>('select'));
    const select = selects.find((item) =>
      Array.from(item.options).some((option) => option.value === 'text'),
    );
    if (!select) throw new Error('工具栏里找不到语言下拉');
    return (select.selectedOptions[0]?.textContent ?? '').trim();
  }

  it('语言下拉：`.txt` 一档不再显示「CSV」；各语言的可见文本互不相同（改造前 `text` 档恒为「CSV」）', () => {
    // 口径（设计 §15.2.5）：**语言下拉**列的是**语言 id**，`.csv`/`.tsv` 在列表里合并显示
    // 「纯文本」——这是"只有一个 `text` id 的必然"；判据（AC-74⑦）针对的是**状态栏的语言名**，
    // 后者按**路径扩展名**取值（由下方同一 describe 的状态栏用例覆盖）。
    const expectedByLanguage: Record<string, string> = {
      text: '纯文本',
      json: 'JSON',
      python: 'Python',
      diff: '差异对比',
    };
    const seen: string[] = [];
    for (const [language, expected] of Object.entries(expectedByLanguage)) {
      const handle = renderEditorTab({ language, detected: language });
      const text = selectedLanguageText(handle.container);
      // 改造前：`language:'text'` ⇒ 显示「CSV」（取 TYPES 里首个 text 项的 label）
      expect(text, `语言 ${language} 的下拉显示名`).toBe(expected);
      expect(text, `语言 ${language} 回退成了裸 id`).not.toBe(language);
      seen.push(text);
      cleanup();
      resetMockEditorSessions();
      resetScratchStore();
    }
    expect(new Set(seen).size, '各语言显示名必须互不相同').toBe(seen.length);
  });

  it('状态栏语言名（AC-74⑦ 的主判据）：`.txt` ⇒「纯文本 (txt)」、`.csv` ⇒「CSV」、`.tsv` ⇒「TSV」，三者互不相同', () => {
    // 状态栏取的是 `resolveTypeForPath(path).label`（与类型表天然同源）—— 这正是 `.csv` / `.tsv`
    // 能区分、而 `.txt` 不再显示「CSV」的原因（改造前它直接渲染语言 id `text`）。
    const cases: { path: string; expected: string }[] = [
      { path: 'D:/tmp/a.txt', expected: '纯文本 (txt)' },
      { path: 'D:/tmp/a.csv', expected: 'CSV' },
      { path: 'D:/tmp/a.tsv', expected: 'TSV' },
      { path: 'D:/tmp/a.json', expected: 'JSON' },
      { path: 'D:/tmp/a.py', expected: 'Python' },
    ];
    const seen: string[] = [];
    for (const { path, expected } of cases) {
      const handle = renderEditorTab({ language: 'text', detected: 'text', path });
      const status = handle.container.querySelector('[role="status"][aria-label="编辑器状态"]');
      if (!status) throw new Error('找不到编辑器状态栏');
      const languageItem = Array.from(status.querySelectorAll('.scratch-status-item')).find(
        (node) => node.getAttribute('title') === '语言（可手动切换）',
      );
      expect(languageItem, '状态栏缺「语言」一项').toBeTruthy();
      const text = (languageItem?.textContent ?? '').trim();
      expect(text, `${path} 的状态栏语言名`).toBe(expected);
      if (!path.endsWith('.csv')) {
        // AC-74⑦ 的误标判据：只有 `.csv` 才该叫「CSV」
        expect(text, `${path} 不得显示「CSV」`).not.toBe('CSV');
      }
      seen.push(text);
      cleanup();
      resetMockEditorSessions();
      resetScratchStore();
    }
    expect(new Set(seen).size, '.txt / .csv / .tsv / .json / .py 必须互不相同').toBe(seen.length);
    expect(seen).not.toContain('text');
  });

  it('草稿形态（无路径）回退到语言名，不编造扩展名', () => {
    const handle = renderEditorTab({ language: 'text', detected: 'text', path: null });
    const status = handle.container.querySelector('[role="status"][aria-label="编辑器状态"]');
    const languageItem = Array.from(status?.querySelectorAll('.scratch-status-item') ?? []).find(
      (node) => node.getAttribute('title') === '语言（可手动切换）',
    );
    expect((languageItem?.textContent ?? '').trim()).toBe('纯文本');
  });

  it('手动切到 `python` 后显示「Python」，且「恢复自动」也显示识别语言的显示名', () => {
    const handle = renderEditorTab({ language: 'python', detected: 'text', path: 'D:/tmp/a.py' });
    const select = Array.from(handle.container.querySelectorAll<HTMLSelectElement>('select')).find(
      (item) => Array.from(item.options).some((option) => option.value === 'python'),
    );
    if (!select) throw new Error('工具栏里找不到语言下拉');
    expect((select.selectedOptions[0]?.textContent ?? '').trim()).toBe('Python');
    // `language !== detected` ⇒ 多出「恢复自动（…）」一项，其显示名走同一个 languageLabel
    const restore = Array.from(select.options).find((option) => option.value === 'text');
    expect(restore?.textContent ?? '').toContain('恢复自动（纯文本）');
  });
});

// ---- AC-74⑥ SQL 方言下拉的中文显示名 ----------------------------------------------

describe('AC-74⑥ SQL 方言下拉：逐项显示名 ≠ 裸 id（覆盖运行时全集）', () => {
  /** 打开工具箱 → SQL 项 → 读方言 `<select>` 的全部可见文本与 value */
  function dialectOptions(container: HTMLElement, tabId: string): { text: string; value: string }[] {
    void tabId; // 方言表已由 `renderEditorTab` 的夹具注入（真离线包）
    openToolbox();
    const item = toolboxItem(container, 'sql');
    // 方言下拉 = `sql` 项里的第一个 `<select>`（后面是「关键字」下拉）
    const select = item.querySelector<HTMLSelectElement>('select');
    if (!select) throw new Error('SQL 项里找不到方言下拉');
    return Array.from(select.options).map((option) => ({
      text: (option.textContent ?? '').trim(),
      value: option.value,
    }));
  }

  it('每个 `<option>` 的可见文本都不等于其内部英文 id（AC-74⑥ 的字面判据）', () => {
    const handle = renderEditorTab({ language: 'sql', detected: 'sql' });
    const options = dialectOptions(handle.container, handle.tabId);
    expect(options.length, '方言项数应等于运行时全集').toBe(
      loadCmBundle().formatters.sqlDialects.length,
    );
    const bare = options.filter((option) => option.text === option.value);
    expect(bare.map((option) => option.value), '仍有显示名等于裸 id 的方言').toEqual([]);
    // `value` 仍是原始 id ⇒ 格式化行为零变化
    expect(options.map((option) => option.value)).toEqual([
      ...loadCmBundle().formatters.sqlDialects,
    ]);
    // 抽查两条口径：专有名词带固定大小写；中文名那两项含中日韩字符
    const byValue = new Map(options.map((option) => [option.value, option.text]));
    expect(byValue.get('mysql')).toBe('MySQL（mysql）');
    expect(byValue.get('postgresql')).toBe('PostgreSQL（postgresql）');
    expect(/[\u4e00-\u9fff]/.test(byValue.get('plsql') ?? '')).toBe(true);
  });
});

// ---- AC-76 工具箱关闭行为 ----------------------------------------------------------

describe('AC-76① 关闭组（10 项）：选中后弹框自动关闭', () => {
  /**
   * 每项的**按钮文案 + 夹具**（按钮选"会改正文 / 会切视图 / 会开覆盖层"的那个，
   * 保证"动作真的发生了"这半边判据有区分度）。
   *
   * 夹具里的正文字面量是按各动作的前置条件选的（`formatJson` 要求合法 JSON、
   * XML 格式化要求合法 XML、SQL 美化要过 `checkSqlFormattable`），
   * 否则动作会走"只提示、不改原文"的失败分支，关框判据就会与"动作是否生效"混在一起。
   */
  const CASES: readonly { id: string; button: string; init?: Partial<ScratchTab>; text: string }[] = [
    { id: 'search', button: '查找 / 替换面板', text: 'b\na\n' },
    { id: 'lines', button: '排序', text: 'b\na\n' },
    { id: 'case', button: '大写', text: 'b\na\n' },
    { id: 'escape', button: 'JSON 转义', text: 'b\na\n' },
    { id: 'base64', button: 'Base64 编码', text: 'b\na\n' },
    { id: 'json', button: '格式化', init: { language: 'json', detected: 'json' }, text: '{"a":1}' },
    {
      id: 'markdown',
      button: '预览',
      init: { language: 'markdown', detected: 'markdown' },
      text: '# T\n',
    },
    { id: 'xml', button: '格式化', init: { language: 'xml', detected: 'xml' }, text: '<a><b>1</b></a>' },
    { id: 'diff', button: '打开对比', text: 'b\na\n' },
  ];

  /** "动作真的发生了"的可观察信号（只在**真的执行**时置真，不只是按钮变灰） */
  function actionApplied(
    itemId: string,
    buttonLabel: string,
    handle: TabRenderHandle,
    originalText: string,
  ): boolean {
    if (itemId === 'search') {
      // 查找面板是 CM6 的 UI 面板：替身宿主没有真实 view，故这里用"面板可用"这一条前置
      // （`session.openSearchPanel()` 存在即代表宿主已挂载；真正"面板打开"由真机 r13 判据覆盖）
      return typeof mockSessionOf(handle.tabId).openSearchPanel === 'function';
    }
    if (itemId === 'diff') {
      // diff 是覆盖层：`ScratchToolbar` 渲染 `.absolute.inset-0`（`views/DiffView` 的容器）
      return handle.container.querySelector('.absolute.inset-0') !== null;
    }
    if (itemId === 'markdown') return handle.tab().viewMode !== 'edit';
    if (itemId === 'csv') return handle.tab().viewMode === 'table';
    if (buttonLabel === '编辑') return handle.tab().viewMode === 'edit';
    // 其余都是"改正文"类：正文变了才算生效
    return tabTextOf(handle.tabId) !== originalText;
  }

  for (const { id: itemId, button: buttonLabel, init, text } of CASES) {
    it(`${itemId}：点「${buttonLabel}」后弹框关闭`, () => {
      const handle = renderEditorTab(init ?? {}, text);
      const toggle = openToolbox();
      const item = toolboxItem(handle.container, itemId);
      const button = buttonOf(item, buttonLabel);
      expect(button.disabled, `夹具前提：${itemId} 的按钮「${buttonLabel}」应可用`).toBe(false);

      act(() => {
        pressButton(button);
      });

      expect(toggle.getAttribute('aria-expanded'), `${itemId} 选完必须关框（AC-76①）`).toBe(
        'false',
      );
      expect(
        actionApplied(itemId, buttonLabel, handle, text),
        `${itemId} 的动作未被应用（判据失去区分度）`,
      ).toBe(true);
    });
  }

  /**
   * 两项的起点**不能**由 `createTab(init)` 摆（已实测）：
   *   - `createTab` 的 `Partial<ScratchTab>` 会**丢弃** `viewMode`（只认它显式读取的字段）
   *     ⇒ 想从 `preview` 起点点「编辑」做不到；改用"先点「预览」再点「编辑」"造出真实往返；
   *   - `table` 能力来自**扩展名**（`capabilitiesForTab`：有路径按 ext 查表）⇒ csv 夹具要给 path。
   */
  it('csv：点「表格视图」后弹框关闭，且视图真的切到 table（能力来自 `.csv` 扩展名）', () => {
    const handle = renderEditorTab(
      { language: 'text', detected: 'text', path: 'D:/tmp/a.csv' },
      'a,b\n1,2\n',
    );
    const toggle = openToolbox();
    const button = buttonOf(toolboxItem(handle.container, 'csv'), '表格视图');
    expect(button.disabled, '夹具前提：`.csv` 路径应给出 table 能力').toBe(false);

    act(() => {
      pressButton(button);
    });

    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(handle.tab().viewMode).toBe('table');
  });

  it('markdown：「预览」⇒ 关框且切到 preview；再开框点「编辑」⇒ 关框且切回 edit', () => {
    const handle = renderEditorTab(
      { language: 'markdown', detected: 'markdown' },
      '# T\n',
    );
    const toggle = openToolbox();
    act(() => {
      pressButton(buttonOf(toolboxItem(handle.container, 'markdown'), '预览'));
    });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(handle.tab().viewMode).toBe('preview');

    openToolbox();
    act(() => {
      pressButton(buttonOf(toolboxItem(handle.container, 'markdown'), '编辑'));
    });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(handle.tab().viewMode).toBe('edit');
  });
});

describe('AC-76② 保持打开组（`sql` / `font`）：连续操作之间弹框仍在', () => {
  it('`font`：连点两次「A+」后弹框仍可见，且字号累计变化', () => {
    const handle = renderEditorTab({}, '');
    const toggle = openToolbox();
    const item = toolboxItem(handle.container, 'font');
    const plus = buttonOf(item, /^A\+$/);
    const before = handle.tab().fontSize;

    act(() => {
      pressButton(plus);
    });
    expect(toggle.getAttribute('aria-expanded'), '`font` 组必须保持打开（AC-76②）').toBe('true');

    act(() => {
      pressButton(plus);
    });
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(handle.tab().fontSize).toBe(before + 4);
  });

  it('`font`：点「行号」后弹框仍可见，且行号开关真的翻转', () => {
    const handle = renderEditorTab({}, '');
    const toggle = openToolbox();
    const before = handle.tab().showLineNumbers;
    act(() => {
      // 文案随状态在「行号」/「行号 ✓」之间切换（`ScratchToolbar.tsx` 的 font 组）
      pressButton(buttonOf(toolboxItem(handle.container, 'font'), /^行号/));
    });
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(handle.tab().showLineNumbers).toBe(!before);
  });

  it('`sql`：点「美化」后弹框仍可见（配置控件在弹框内，需连续操作）', () => {
    const handle = renderEditorTab({ language: 'sql', detected: 'sql' }, 'select 1');
    const toggle = openToolbox();
    const item = toolboxItem(handle.container, 'sql');
    act(() => {
      pressButton(buttonOf(item, '美化'));
    });
    expect(toggle.getAttribute('aria-expanded'), '`sql` 组必须保持打开（AC-76②）').toBe('true');
    // 美化真的执行了（正文变化）
    expect(tabTextOf(handle.tabId)).not.toBe('select 1');
  });
});

describe('AC-76③④⑤ `Esc` / 点外部 / 关框不改正文', () => {
  it('`Esc` ⇒ 弹框关闭，且正文逐字符不变', () => {
    const handle = renderEditorTab({}, 'keep me\n');
    const toggle = openToolbox();

    act(() => {
      fireEvent.keyDown(window, { key: 'Escape' });
    });

    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(tabTextOf(handle.tabId), '关框不得改正文（AC-76⑤）').toBe('keep me\n');
  });

  it('点弹框外部（面板外的目标）⇒ 弹框关闭；点弹框内部不算外部', () => {
    const handle = renderEditorTab({}, 'keep me\n');
    const toggle = openToolbox();
    const menu = handle.container.querySelector<HTMLElement>('[data-toolbox-menu="true"]');
    expect(menu, '找不到弹框容器').not.toBeNull();

    // ① 弹框**内部**的 pointerdown 不关（后续由容器委托决定）
    act(() => {
      fireEvent.pointerDown(menu as HTMLElement);
    });
    expect(toggle.getAttribute('aria-expanded'), '点在弹框内不应关闭').toBe('true');

    // ② 弹框**外部**（面板外的节点）⇒ 关
    const outside = document.createElement('div');
    document.body.appendChild(outside);
    act(() => {
      fireEvent.pointerDown(outside);
    });
    expect(toggle.getAttribute('aria-expanded'), '点外部必须关闭（AC-76④）').toBe('false');
    expect(tabTextOf(handle.tabId)).toBe('keep me\n');
    outside.remove();
  });

  it('点「工具箱」开关本身不算"外部"（由开关自己的切换语义处理）', () => {
    renderEditorTab({}, '');
    const toggle = openToolbox();
    act(() => {
      fireEvent.pointerDown(toggle);
    });
    expect(toggle.getAttribute('aria-expanded'), '开关上的 pointerdown 不应直接关闭').toBe('true');
    act(() => {
      fireEvent.click(toggle);
    });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
  });

  it('点击**禁用**按钮不关框（只读标签页：用户没拿到任何结果，不该丢上下文）', () => {
    const handle = renderEditorTab({ readOnly: true }, 'ro\n');
    const toggle = openToolbox();
    const item = toolboxItem(handle.container, 'lines');
    const button = buttonOf(item, '排序');
    expect(button.disabled, '夹具前提：只读标签页的按钮应禁用').toBe(true);

    act(() => {
      pressButton(button);
    });

    expect(toggle.getAttribute('aria-expanded'), '禁用按钮不得触发关框').toBe('true');
    expect(tabTextOf(handle.tabId)).toBe('ro\n');
  });

  it('点 `<input>`（跳转行输入框）不关框（只有可用 `<button>` 才关）', () => {
    const handle = renderEditorTab({}, 'a\nb\n');
    const toggle = openToolbox();
    const input = toolboxItem(handle.container, 'search').querySelector('input');
    expect(input, '找不到跳转行输入框').not.toBeNull();

    act(() => {
      fireEvent.pointerDown(input as HTMLInputElement);
      fireEvent.pointerUp(input as HTMLInputElement);
    });

    expect(toggle.getAttribute('aria-expanded'), '点到输入框不应关框').toBe('true');
  });
});

// ---- 键盘接管：`Ctrl+K` / `Ctrl+Shift+D` 真的把面板打开（AC-75②） -----------------------

describe('AC-75② 面板请求真的能打开弹框（闭环 BL-51）', () => {
  it('运行时投递 `toolbox` 请求后，目标面板挂载即打开弹框', async () => {
    const { useScratchUiStore } = await import('../../../stores/scratch-ui-store');
    const handle = renderEditorTab({}, '');
    const toggle = toolboxToggle();
    expect(toggle.getAttribute('aria-expanded'), '初始应为关闭').toBe('false');

    act(() => {
      // 等价于 `Ctrl+K` 被运行时接管后的最后一步（`shortcut-runtime.ts` 的 requestPanel 分支）
      useScratchUiStore.getState().requestPanel('toolbox', handle.tabId);
    });

    expect(toggle.getAttribute('aria-expanded'), '`Ctrl+K` 必须真的让弹框出现').toBe('true');
  });

  it('请求的目标不是本面板 ⇒ 本面板不响应（多面板并存时不误开）', async () => {
    const { useScratchUiStore } = await import('../../../stores/scratch-ui-store');
    renderEditorTab({}, '');
    const toggle = toolboxToggle();

    act(() => {
      useScratchUiStore.getState().requestPanel('toolbox', 'another-tab-id');
    });

    expect(toggle.getAttribute('aria-expanded')).toBe('false');
  });

  it('`diff` 请求 ⇒ 打开 diff 覆盖层（正文来自会话，未挂载时给中文提示）', async () => {
    const { useScratchUiStore } = await import('../../../stores/scratch-ui-store');
    const handle = renderEditorTab({}, 'x\ny\n');

    act(() => {
      useScratchUiStore.getState().requestPanel('diff', handle.tabId);
    });

    // `DiffView` 的覆盖层容器（`ScratchToolbar.tsx` 的 `absolute inset-0 z-30`）
    expect(handle.container.querySelector('.absolute.inset-0')).not.toBeNull();
    expect(useScratchStore.getState().tabs).toHaveLength(1);
  });
});
