/**
 * 宿主级判据：`blockSelectKeymap` 真的抢过了 `defaultKeymap`（AC-13 / BL-32）
 *
 * ## 为什么单独立一个文件（审查 M3）
 *
 * `utils/scratch/__tests__/block-select-keys-host.test.ts` 钉的是**纯函数**几何；
 * 它原先的注释声称"宿主接线由 `scratch-editor-host-*` 那组用例覆盖"——**那个文件并不存在**。
 * 于是下面这三件事一度**没有任何自动化判据**（只有 r11 真机探针）：
 *
 * 1. `Prec.highest(keymap.of(...))` 是否真的让宿主的四个键位排到 `defaultKeymap` **之前**
 *    （修复前 `Alt+Shift+↓` 落到 `copyLineDown`，真机实测每按一次复制 2 行并写回磁盘）；
 * 2. `keymap.run` 的语义：命中后**只派发选区**（`changes` 必须为空），不碰文档；
 * 3. `WeakMap<EditorView, BlockCursor>` 的**续锚**：`→×2 + ↓×2` 这条链上单行块反推不出列宽，
 *    全靠记住上一轮锚点，最终复制口径必须等于 AC-11 的 `bcd\nh  \njkl`。
 *
 * ## 判据为什么用「真 `keymap` facet」而不是替身
 *
 * `state.facet(api.extensions.keymap)` 给出的是**按扩展优先级排好序**的真实绑定表，
 * 且 `run` 在 `{state, dispatch}` 假视图上真跑（`dispatch` 把事务回写进 state，
 * 于是多次按键可以在同一个"视图"上连续推演）。离线包由
 * `src/test/cm-bundle.ts` 注入 `public/vendor/editor.bundle.js`（真内核，不是替身）。
 *
 * 若 `Prec.highest` **没**生效，`bindings[0]` 就会是 `copyLineDown`：它同样返回 `true`，
 * 但派发的事务里带 `changes` ⇒ 断言立刻变红 —— 这就是本文件"有牙"的地方。
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { loadCmBundle } from '../../../test/cm-bundle';
import { BLOCK_SELECT_AC11, BLOCK_SELECT_AC13, blockSelectCopy } from '../../../utils/scratch/block-select';
import { blockSelectKeymap } from '../ScratchEditorHost';
import type {
  EditorSelection,
  EditorState,
  EditorView,
  JFLoveCmApi,
  KeyBinding,
  SelectionRange,
  Text,
  Transaction,
  TransactionSpec,
} from '../../../types/codemirror-bundle';

/** 离线包里的 `EditorSelection`（手写类型把 `modules.state` 声明为宽松索引签名） */
interface EditorSelectionStatic {
  range(from: number, to: number): SelectionRange;
  create(ranges: readonly SelectionRange[], mainIndex?: number): EditorSelection;
}

let api: JFLoveCmApi;
let selectionApi: EditorSelectionStatic;
let doc: Text;

beforeEach(() => {
  api = loadCmBundle();
  selectionApi = (api.modules.state as unknown as { EditorSelection: EditorSelectionStatic })
    .EditorSelection;
  doc = api.EditorState.create({ doc: BLOCK_SELECT_AC11.doc }).doc;
});

/**
 * 键名字符串的**修饰键顺序**在 CM6 里不敏感：
 * 宿主写 `Alt-Shift-ArrowRight`，而 `defaultKeymap` 里写 `Shift-Alt-ArrowRight`。
 */
function sameChord(a: unknown, b: string): boolean {
  // 真 `defaultKeymap` 里存在**只有 `mac`、没有 `key`** 的绑定 ⇒ 这里必须容忍非字符串
  if (typeof a !== 'string' || typeof b !== 'string') return false;
  return a.split('-').sort().join('-') === b.split('-').sort().join('-');
}

/**
 * 与宿主 `baseExtensions()` 同款装配：本组键位 + **真** `defaultKeymap`（两者争同一个键）。
 *
 * ⚠ `allowMultipleSelections` 必须开（宿主 `baseExtensions()` 里就有这一条）：
 * 不开时 CM6 会把多 range 选区**塌成主选区**，多按一次 `↓` 就会因锚点不匹配而重新起锚
 * （本文件第一版就踩了这个坑，症状是最终只剩一个 range）。
 */
function stateWithDefaultKeymap(seedPos: number, modifier = 'Alt'): EditorState {
  return api.EditorState.create({
    doc: BLOCK_SELECT_AC11.doc,
    selection: { anchor: seedPos },
    extensions: [
      api.EditorState.allowMultipleSelections.of(true),
      blockSelectKeymap(api, modifier),
      api.extensions.keymap.of([...api.modules.commands.defaultKeymap]),
    ],
  });
}

/**
 * 命中某键名的全部绑定（顺序 = facet 的优先级顺序）。
 *
 * ⚠ 真包实测：`state.facet(keymap)` 是**数组的数组**（每个扩展的 `KeyBinding[]` 各占一项，
 * 外层按 `Prec` 优先级排序）⇒ 必须 `flat()` 之后才是绑定表本身。
 * 手写类型 `codemirror-bundle.d.ts` 的 `JFLoveCmExtensions.keymap` 已按实测同步。
 */
function bindingsFor(state: EditorState, chord: string): readonly KeyBinding[] {
  // `keymap` 分片的**读取形状**是"数组的数组"（外层按扩展优先级排序、`Prec.highest` 在前），
  // 手写类型按写入侧声明成 `readonly KeyBinding[]` ⇒ 这里显式收窄后再 `flat()`
  // （依据：`types/codemirror-bundle.d.ts` 的 `keymap` 注释 + 本文件用真包实测）。
  const groups = state.facet(api.extensions.keymap) as unknown as readonly (readonly KeyBinding[])[];
  return groups.flat().filter((binding) => sameChord(binding.key, chord));
}

/** 只需要 `state` + `dispatch` 的假视图（`run` 的真语义就是这两者） */
interface FakeView {
  state: EditorState;
  specs: TransactionSpec[];
  view: EditorView;
}

function makeFakeView(state: EditorState): FakeView {
  const holder: Partial<FakeView> = { state, specs: [] };
  holder.view = {
    get state() {
      return holder.state as EditorState;
    },
    dispatch: (...specs: TransactionSpec[]) => {
      (holder.specs as TransactionSpec[]).push(...specs);
      const transaction: Transaction = (holder.state as EditorState).update(...specs);
      holder.state = transaction.state;
    },
  } as unknown as EditorView;
  return holder as FakeView;
}

describe('blockSelectKeymap：Prec.highest 抢键位（审查 M3 的核心判据）', () => {
  it('Alt-Shift-ArrowDown 在 facet 里排第一，且命中"只改选区、不改文档"的语义', () => {
    const state = stateWithDefaultKeymap(1);
    const bindings = bindingsFor(state, 'Alt-Shift-ArrowDown');
    // 我们的 + defaultKeymap 的 copyLineDown 至少两条（后者是修复前的实际命中者）
    expect(bindings.length).toBeGreaterThanOrEqual(2);

    const fake = makeFakeView(state);
    expect(bindings[0].run?.(fake.view)).toBe(true);
    const spec = fake.specs[0];
    // 我们的语义只派发选区；copyLineDown 会带 changes ⇒ 这一条就是"谁赢了"的判据
    expect(spec.changes).toBeUndefined();
    expect(spec.selection).toBeDefined();
  });

  it('Ctrl 修饰键同样排第一（键名随设置切换，优先级不依赖具体键名）', () => {
    const state = stateWithDefaultKeymap(1, 'Ctrl');
    const bindings = bindingsFor(state, 'Ctrl-Shift-ArrowRight');
    expect(bindings.length).toBeGreaterThanOrEqual(1);
    const fake = makeFakeView(state);
    expect(bindings[0].run?.(fake.view)).toBe(true);
    expect(fake.specs[0].changes).toBeUndefined();
    expect(fake.specs[0].selection).toBeDefined();
  });
});

describe('keymap.run 的连续按键链（WeakMap 续锚）', () => {
  it('→×2 + ↓×2 逐次真跑 run ⇒ 3 行各一个 range，复制口径等于 AC-11 的 `bcd\\nh  \\njkl`', () => {
    const seedPos = doc.line(BLOCK_SELECT_AC13.seedLine).from + BLOCK_SELECT_AC13.seedCol;
    const fake = makeFakeView(stateWithDefaultKeymap(seedPos));

    for (const direction of BLOCK_SELECT_AC13.keys) {
      const chord = `Alt-Shift-Arrow${direction[0].toUpperCase()}${direction.slice(1)}`;
      const binding = bindingsFor(fake.state, chord)[0];
      expect(binding, `未找到键位 ${chord}`).toBeDefined();
      expect(binding.run?.(fake.view), `${chord} 未被本组键位接管`).toBe(true);
      // 每一步都只能改选区（真机上"改文档 + 自动保存写回"是 D-2 的数据破坏面）
      expect(fake.specs.at(-1)?.changes).toBeUndefined();
    }

    const ranges = fake.state.selection.ranges;
    expect(ranges.map((range) => [range.from, range.to])).toEqual([
      [1, 4],
      [8, 9], // 中间行 `gh` 只有 2 字符 ⇒ 行尾夹短，补空格由 blockSelectCopy 负责
      [11, 14],
    ]);
    expect(blockSelectCopy(fake.state.doc, ranges)).toBe(BLOCK_SELECT_AC13.expected);
    // 与鼠标路径（AC-11 / r11 H6）完全同一条拼接口径
    expect(blockSelectCopy(fake.state.doc, ranges)).toBe('bcd\nh  \njkl');
  });

  it('单行块也走同一条通路（1 个 range，宽度记住）', () => {
    const fake = makeFakeView(stateWithDefaultKeymap(1));
    const binding = bindingsFor(fake.state, 'Alt-Shift-ArrowRight')[0];
    expect(binding.run?.(fake.view)).toBe(true);
    const ranges = fake.state.selection.ranges;
    expect(ranges).toHaveLength(1);
    expect([ranges[0].from, ranges[0].to]).toEqual([1, 3]);
    // 选区形状合法（CM6 的多选区必须带 allowMultipleSelections —— 宿主已开）
    expect(selectionApi.create([...ranges]).ranges).toHaveLength(1);
  });
});
