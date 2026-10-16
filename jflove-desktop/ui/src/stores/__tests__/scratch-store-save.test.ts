/**
 * 标签页 store「保存 / 自动暂存 / 关闭决策」语义负向对照 —— Phase 6（v1.6.1）
 *
 * ## 迁移说明（v1.6.0 浮动窗格 → v1.6.1 标签页）
 *
 * 设计 v1.6.1 §4.3（`文档记录/设计文档/v1.6.1.md:446-480`）把容器语义整体改写：
 * `panes` → `tabs`、`activePaneId` → `activeTabId`、`createPane` → `createTab`、
 * `closePane` → `closeTab`、`focusPane` → `setActiveTab`、`applyReadResult` → `openOrFocus`，
 * 并**删除**全部几何 / 排列类 action（`movePane` / `resizePane` / `toggleMinimize` /
 * `toggleMaximize` / `tileAll` / `cascadeAll` / `setViewport`，判据 grep = 0）。
 * 本文件原断言的是**保存通路与 store 决策**，与几何无关 ⇒ 逐条改写为标签页语义下的**等价判据**，
 * 唯一"无等价物"的旧判据是窗格几何（由设计 §10.3 直接作废），本文件从未覆盖它。
 * `savePane` 的语义与**全部失败出口**（含"写盘失败后重新排期自动暂存"的回归点）在设计里
 * 声明为**逐字保留**（`:474`、`:689`），因此本文件对它的覆盖**一条不减**。
 *
 * ## 被测对象
 *
 * `stores/scratch-store.ts` 的**唯一落盘通路**：
 *
 * ```
 * markDirty(id, true) → scheduleFlush(id)                     // scratch-store.ts:282-291 / :489
 *   └─ AUTO_SAVE_DEBOUNCE_MS（1500，scratch-store.ts:19-21 同值常量）后
 *      flushPane(id)                                           // scratch-store.ts:298-346
 *        ① 总是 scratchService.draftSave(...)                   // 草稿库（两种形态的唯一/共同落点）
 *        ② tab.path !== null && !tab.readOnly 才 writeText      // 用户文件（AC-40 / AC-19）
 *        ③ wroteUserFile && 期间无新编辑 ⇒ dirty=false           // AC-54 的"不重写"前提
 * flushPendingSaves()                                           // scratch-store.ts:609-613
 *       = 立刻触发所有待写标签（关闭询问的「保存」与退出前落盘都走它）
 * savePane(id, { forceDialog })                                 // scratch-store.ts:615-715（BL-33）
 *       = `Ctrl+S` / `Ctrl+Shift+S` 的落点（`utils/scratch/shortcut-runtime.ts:122-130`）：
 *         已有路径直接写回；草稿形态先 pickSaveTarget（取消 ⇒ 不写、仍为脏）；
 *         只读 / 未挂载 ⇒ 拒绝并给出可读错误
 * requestCloseTab(id) / clearCloseRequest()                     // scratch-store.ts:720-731
 *       = AC-59 关闭脏标签的「保存 / 放弃 / 取消」三分支在 store 侧的载体：
 *         脏 ⇒ 置 pendingCloseId（由工作区渲染询问框）；干净 ⇒ 直接 closeTab
 * ```
 *
 * ## 判据出处（需求原文）
 *
 * - AC-37：「已有路径的文档保存不再询问：已保存文档（目标 `p`）内容改为 `y` → 按 `Ctrl+S` →
 *   ① 全程不出现任何要求选择位置或文件名的对话框；② `p` 内容等于 `y`；③ 所在目录的文件数量与
 *   文件名集合不变（无副本、无新文件）」⇒ 本例断言：**0 次对话框调用 + 恰好 1 次写回同一路径**。
 * - AC-36：「草稿形态首次保存必须由用户指定路径与文件名 …**取消对话框** → 磁盘上不产生任何文件、
 *   标签页仍为脏」⇒ 本例断言：先 `pickSaveTarget` 恰好 1 次，取消后 0 次写入且仍脏。
 * - AC-40：「自动保存只对已有路径的文档生效 ② 草稿形态（从未保存）标签页输入 `c` → 等待 ≥ 2 倍防抖
 *   间隔 → 用户文件系统上没有任何文件被写入 `c`」⇒ 本例断言：3000 ms 后 `writeText` 次数为 0。
 * - AC-54：「输入内容后不保存 → 等待设计文档规定的自动暂存间隔 + 余量后，本机草稿存在且内容一致；
 *   随后内容不变再等一个周期 → 草稿文件 mtime 不变（未重复写）」⇒ 到期判据 = 1500 ms；
 *   1499 ms 不触发、1500 ms 触发；干净（不脏）时到期不产生写入。
 * - AC-19 / AC-52：「`.diff` / `.patch` 以只读方式打开…且保存不可用」/「`> 5 MB` 只读打开…
 *   保存不可用」⇒ 本例断言：只读标签页在任何保存通路上 `writeText` 次数为 0。
 * - AC-59：「关闭**脏标签页** → 出现询问（保存 / 放弃 / 取消）；选**取消** → 标签页仍在且内容不变；
 *   选**放弃** → 标签页关闭且该草稿被清理」⇒ 本例断言 `requestCloseTab` / `clearCloseRequest`
 *   与三个分支各自落到 store 上的可观测结果（UI 侧点击口径见
 *   `src/__tests__/scratch-save-decision.test.tsx` 的 AC-59 组）。
 * - AC-65 / §4.3 不变量①：「同路径（`path` 非空且相等）→ 聚焦已有标签，不新建、不覆盖内容」
 *   ⇒ 本例断言 `openOrFocus` 的返回值 / `tabs.length` / 激活项 / 初始正文三处证据。
 *
 * ## 分层声明
 *
 * 只断言 **UI 决策层**（该调哪个桥方法、几个回合、什么参数）；真实文件系统行为
 * （文件是否落盘、mtime、sha256）由 Python 侧探针覆盖，这里全部用 `vi.mock` 替身化，
 * **不写任何真实文件**。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/scratch-service', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/scratch-service')>();
  return {
    ...actual,
    // 只替身化"会碰文件系统/原生对话框"的六个方法，其余（含 toDraftPaneMeta）保持真实实现
    scratchService: {
      ...actual.scratchService,
      draftSave: vi.fn(),
      draftDiscard: vi.fn(),
      writeText: vi.fn(),
      reportDirty: vi.fn(),
      pickSaveTarget: vi.fn(),
      openDialog: vi.fn(),
    },
  };
});

import { scratchService } from '../../services/scratch-service';
import {
  AUTO_SAVE_DEBOUNCE_MS,
  registerScratchContentAccessor,
  takeInitialText,
  useScratchStore,
} from '../scratch-store';
import type { ReadItemOk, ScratchTab } from '../../types/scratch';

const draftSave = vi.mocked(scratchService.draftSave);
const writeText = vi.mocked(scratchService.writeText);
const reportDirty = vi.mocked(scratchService.reportDirty);
const pickSaveTarget = vi.mocked(scratchService.pickSaveTarget);
const openDialog = vi.mocked(scratchService.openDialog);
const draftDiscard = vi.mocked(scratchService.draftDiscard);

/** 本用例创建过的标签页（afterEach 统一 close，清掉 store 的模块级旁路表与计时器） */
const createdTabIds: string[] = [];
/** 正文读取器注销函数（与标签页一一对应） */
const accessorReleases: Array<() => void> = [];

/** 建标签页并登记到清理清单 */
function newTab(init: Partial<ScratchTab>): string {
  const id = useScratchStore.getState().createTab(init);
  createdTabIds.push(id);
  return id;
}

/**
 * 注册正文读取器（对齐 `EditorPanel` → `ScratchEditorHost` 挂载时的做法）。
 *
 * `flushPane` 读不到读取器就**直接返回**（`scratch-store.ts:298-303`），所以每个要落盘的标签页
 * 必须先注册 —— 这也让"未挂载标签页只有元数据、没有正文"这条边界在本文件里可复现。
 */
function registerContent(tabId: string, text: () => string): void {
  accessorReleases.push(
    registerScratchContentAccessor(tabId, () => ({
      text: text(),
      caret: { line: 1, col: 1 },
      scrollTop: 0,
    })),
  );
}

/** 推进定时器并让 `flushPane` 的异步链（await 桥调用）走完 */
async function advance(ms: number): Promise<void> {
  await vi.advanceTimersByTimeAsync(ms);
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
}

/** 取标签页当前状态 */
function tabOf(id: string): ScratchTab {
  const tab = useScratchStore.getState().tabs.find((item) => item.id === id);
  if (!tab) throw new Error(`标签页不存在：${id}`);
  return tab;
}

/** 造一条 `detectAndRead` 成功条目（字段名逐字对齐 `types/scratch.ts:100-111`） */
function readItemOf(path: string, text = '正文'): ReadItemOk {
  return {
    path,
    ok: true,
    name: path.split('/').pop() ?? path,
    text,
    size: text.length,
    language: 'text',
    encoding: 'utf-8',
    lineEnding: 'lf',
    readOnly: false,
    warnings: [],
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  // 模块级 mock 的默认实现（`restoreMocks` 只恢复 spy，这里显式重置更稳）
  draftSave.mockResolvedValue({ ok: true, at: 1 });
  draftDiscard.mockResolvedValue({ ok: true });
  writeText.mockResolvedValue({ ok: true, size: 1, mtime: 1, sha256: 'x' });
  reportDirty.mockResolvedValue({ ok: true });
  pickSaveTarget.mockResolvedValue(null); // 取消对话框
  openDialog.mockResolvedValue({ cancelled: true, path: null });
  useScratchStore.setState({
    tabs: [],
    activeTabId: null,
    lastError: null,
    pendingCloseId: null,
  });
});

afterEach(() => {
  accessorReleases.splice(0).forEach((release) => release());
  createdTabIds.splice(0).forEach((id) => useScratchStore.getState().closeTab(id));
  vi.useRealTimers();
});

describe('AC-54 到期判据 = AUTO_SAVE_DEBOUNCE_MS（1500 ms）', () => {
  it('常量本身必须是 1500（需求 D21 裁决：debounce 1.5 s，判据以 1.5 s 为准）', () => {
    expect(AUTO_SAVE_DEBOUNCE_MS).toBe(1500);
  });

  it('推进 1499 ms 不触发；再推进 1 ms（=1500）触发一次草稿写入与一次用户文件写回', async () => {
    const id = newTab({ path: 'D:/tmp/ac54.txt' });
    registerContent(id, () => 'a');
    useScratchStore.getState().markDirty(id, true);

    await advance(1499);
    expect(draftSave).toHaveBeenCalledTimes(0);
    expect(writeText).toHaveBeenCalledTimes(0);

    await advance(1);
    expect(draftSave).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledTimes(1);
  });

  it('内容未变化（不脏）时到期不产生任何写入：落定后无待写计时器，再等 3000 ms 仍为 0 次新增', async () => {
    const id = newTab({ path: 'D:/tmp/ac54-clean.txt' });
    registerContent(id, () => 'a');
    useScratchStore.getState().markDirty(id, true);

    await advance(AUTO_SAVE_DEBOUNCE_MS);
    expect(draftSave).toHaveBeenCalledTimes(1);
    // 写回成功且期间无新编辑 ⇒ 脏标记落下（AC-54 的下一个周期"未重复写"的前提）
    expect(tabOf(id).dirty).toBe(false);

    await advance(AUTO_SAVE_DEBOUNCE_MS * 2);
    expect(draftSave).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledTimes(1);

    // 干净态直接触发批量落盘（关闭询问的「保存」/ 退出前落盘）也不得写入
    await useScratchStore.getState().flushPendingSaves();
    expect(draftSave).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledTimes(1);
  });

  it('只读标签页到期后：草稿库照写（内容不丢），用户文件 0 次写入、脏标记不清（AC-19 同层守门）', async () => {
    const id = newTab({ path: 'D:/tmp/ac19.diff', readOnly: true });
    registerContent(id, () => 'diff 内容');
    useScratchStore.getState().markDirty(id, true);

    await advance(AUTO_SAVE_DEBOUNCE_MS);
    expect(draftSave).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledTimes(0);
    expect(tabOf(id).dirty).toBe(true);
  });

  it('只读标签页走"立刻落盘"通路（flushPendingSaves）同样 0 次写入、脏标记不清（主动保存路径的同层守门）', async () => {
    // 与 M-11（批量保存的**筛选**漏了 readOnly）区分：这里测的是**写回动作本身**的守门
    // —— 即便某条通路把只读标签页送进了落盘流程，`flushPane` 也不得调用 writeText（AC-19 / AC-52）。
    const id = newTab({ path: 'D:/tmp/ac19-explicit.diff', readOnly: true });
    registerContent(id, () => 'diff 内容');
    useScratchStore.getState().markDirty(id, true);

    await useScratchStore.getState().flushPendingSaves();

    expect(writeText).toHaveBeenCalledTimes(0);
    expect(draftSave).toHaveBeenCalledTimes(1); // 草稿库照写：内容不丢
    expect(tabOf(id).dirty).toBe(true);
  });
});

describe('AC-37 已有路径保存：不询问 + 恰好一次写回同一路径（AC-40 ① 同源）', () => {
  it('防抖到期写回原路径一次，全程 0 次对话框（openDialog / pickSaveTarget）', async () => {
    const id = newTab({ path: 'D:/tmp/ac37.txt' });
    registerContent(id, () => 'y');
    useScratchStore.getState().markDirty(id, true);

    await advance(AUTO_SAVE_DEBOUNCE_MS);

    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledWith({
      path: 'D:/tmp/ac37.txt',
      text: 'y',
      encoding: 'utf-8',
      lineEnding: 'lf',
    });
    // AC-37 ①：不得出现任何要求选择位置或文件名的对话框（也不得走另存为）
    expect(openDialog).toHaveBeenCalledTimes(0);
    expect(pickSaveTarget).toHaveBeenCalledTimes(0);
  });

  it('等待 ≥ 2 倍防抖间隔（AC-40 判据）后：写回次数仍为 1，且脏标记落下', async () => {
    const id = newTab({ path: 'D:/tmp/ac40-1.txt' });
    registerContent(id, () => 'b');
    useScratchStore.getState().markDirty(id, true);

    await advance(AUTO_SAVE_DEBOUNCE_MS * 2);

    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText.mock.calls[0]?.[0]?.text).toBe('b');
    expect(tabOf(id).dirty).toBe(false);
  });

  it('批量落盘（flushPendingSaves）对已有路径的待写标签页同样只写一次、不询问', async () => {
    const id = newTab({ path: 'D:/tmp/ac37-flush.txt' });
    registerContent(id, () => 'y');
    useScratchStore.getState().markDirty(id, true);

    await useScratchStore.getState().flushPendingSaves();

    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText.mock.calls[0]?.[0]?.path).toBe('D:/tmp/ac37-flush.txt');
    expect(openDialog).toHaveBeenCalledTimes(0);
    expect(pickSaveTarget).toHaveBeenCalledTimes(0);
    expect(tabOf(id).dirty).toBe(false);
  });
});

describe('AC-40 ② 草稿形态（path === null）：自动暂存只进草稿库，用户文件零写入', () => {
  it('推进 3000 ms（2 × 防抖）：writeText 次数为 0，draftSave 恰好 1 次', async () => {
    const id = newTab({ path: null });
    registerContent(id, () => 'c');
    useScratchStore.getState().markDirty(id, true);

    await advance(AUTO_SAVE_DEBOUNCE_MS * 2);

    expect(writeText).toHaveBeenCalledTimes(0);
    expect(draftSave).toHaveBeenCalledTimes(1);
    // 草稿形态是唯一落点：正文进草稿库，且**不带路径**
    expect(draftSave.mock.calls[0]?.[0]?.path).toBeNull();
    expect(draftSave.mock.calls[0]?.[1]).toBe('c');
  });

  it('走到期也不询问路径（AC-36 的"取消 ⇒ 仍为脏、零写入"同层语义）：path 仍为 null、dirty 仍为 true', async () => {
    const id = newTab({ path: null });
    registerContent(id, () => 'c');
    useScratchStore.getState().markDirty(id, true);

    await advance(AUTO_SAVE_DEBOUNCE_MS * 3);

    expect(tabOf(id).path).toBeNull();
    expect(tabOf(id).dirty).toBe(true);
    expect(writeText).toHaveBeenCalledTimes(0);
    // 自动暂存不得弹原生对话框（弹窗只属于用户显式触发的保存 / 另存为）
    expect(openDialog).toHaveBeenCalledTimes(0);
    expect(pickSaveTarget).toHaveBeenCalledTimes(0);
  });

  it('批量落盘也不会把草稿形态静默写盘（D17 红线）', async () => {
    const id = newTab({ path: null });
    registerContent(id, () => 'c');
    useScratchStore.getState().markDirty(id, true);

    await useScratchStore.getState().flushPendingSaves();

    expect(writeText).toHaveBeenCalledTimes(0);
    expect(tabOf(id).dirty).toBe(true);
  });
});

describe('BL-33 显式保存（savePane）：AC-36 / AC-37 的 store 层判据 + 审查 M4 回归', () => {
  it('已有路径：0 次对话框、恰好 1 次写回原路径、dirty 落 false 且返回 true', async () => {
    const id = newTab({ path: 'D:/tmp/bugfix-save.txt' });
    registerContent(id, () => 'y');
    useScratchStore.getState().markDirty(id, true);

    const ok = await useScratchStore.getState().savePane(id);

    expect(ok).toBe(true);
    expect(pickSaveTarget).toHaveBeenCalledTimes(0);
    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledWith({
      path: 'D:/tmp/bugfix-save.txt',
      text: 'y',
      encoding: 'utf-8',
      lineEnding: 'lf',
    });
    expect(tabOf(id).dirty).toBe(false);
  });

  it('草稿形态：必须先询问路径；取消 ⇒ 0 次写入、仍为脏、path 仍为 null、返回 false', async () => {
    const id = newTab({ path: null });
    registerContent(id, () => 'x');
    useScratchStore.getState().markDirty(id, true);
    pickSaveTarget.mockResolvedValue(null); // 用户取消对话框

    const ok = await useScratchStore.getState().savePane(id);

    expect(pickSaveTarget).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledTimes(0);
    expect(tabOf(id).path).toBeNull();
    expect(tabOf(id).dirty).toBe(true);
    expect(ok).toBe(false);
  });

  it('草稿形态选到路径：写到选定路径、回填 path/title、dirty 落 false', async () => {
    const id = newTab({ path: null });
    registerContent(id, () => 'x');
    useScratchStore.getState().markDirty(id, true);
    pickSaveTarget.mockResolvedValue('D:/tmp/新建文档.txt');

    const ok = await useScratchStore.getState().savePane(id);

    expect(ok).toBe(true);
    expect(writeText).toHaveBeenCalledWith({
      path: 'D:/tmp/新建文档.txt',
      text: 'x',
      encoding: 'utf-8',
      lineEnding: 'lf',
    });
    expect(tabOf(id).path).toBe('D:/tmp/新建文档.txt');
    expect(tabOf(id).title).toBe('新建文档.txt');
    expect(tabOf(id).dirty).toBe(false);
  });

  it('只读标签页：拒绝保存（0 次写入 / 0 次对话框、仍为脏）并给出可读错误（AC-19 / AC-52 守门）', async () => {
    const id = newTab({ path: 'D:/tmp/bugfix-readonly.diff', readOnly: true });
    registerContent(id, () => 'diff');
    useScratchStore.getState().markDirty(id, true);

    const ok = await useScratchStore.getState().savePane(id);

    expect(ok).toBe(false);
    expect(writeText).toHaveBeenCalledTimes(0);
    expect(pickSaveTarget).toHaveBeenCalledTimes(0);
    expect(tabOf(id).dirty).toBe(true);
    expect(useScratchStore.getState().lastError).toContain('只读');
  });

  it('未挂载标签页（无正文读取器）：拒绝保存且提示编辑器未就绪（不得写空内容覆盖文件）', async () => {
    const id = newTab({ path: 'D:/tmp/bugfix-nomount.txt' });
    useScratchStore.getState().markDirty(id, true);

    const ok = await useScratchStore.getState().savePane(id);

    expect(ok).toBe(false);
    expect(writeText).toHaveBeenCalledTimes(0);
    expect(useScratchStore.getState().lastError).toContain('编辑器尚未就绪');
  });

  it('审查 M4 回归：保存失败不得吞掉已排队的自动暂存（失败出口要重新排上防抖）', async () => {
    const id = newTab({ path: 'D:/tmp/bugfix-m4.txt' });
    registerContent(id, () => 'z');
    useScratchStore.getState().markDirty(id, true); // 排上 1500 ms 防抖
    writeText.mockRejectedValueOnce(new Error('桥调用失败：模拟'));

    const ok = await useScratchStore.getState().savePane(id);
    expect(ok).toBe(false);
    expect(draftSave).toHaveBeenCalledTimes(0); // 还没到防抖时刻

    // 修复前：`savePane` 写盘前 clearFlushTimer 又不重排 ⇒ 这里永远等不到自动暂存（M4 的症状）
    await advance(AUTO_SAVE_DEBOUNCE_MS);
    expect(draftSave).toHaveBeenCalledTimes(1);
  });
});

describe('AC-59 关闭脏标签页的三分支决策（requestCloseTab / clearCloseRequest 语义）', () => {
  it('脏标签：requestCloseTab 只置 pendingCloseId，标签仍在、内容不动、零写入、零丢弃', () => {
    const id = newTab({ path: 'D:/tmp/ac59.txt', title: 'ac59.txt', dirty: true });

    useScratchStore.getState().requestCloseTab(id);

    expect(useScratchStore.getState().pendingCloseId).toBe(id);
    expect(useScratchStore.getState().tabs.map((tab) => tab.id)).toEqual([id]);
    expect(tabOf(id).dirty).toBe(true);
    expect(writeText).toHaveBeenCalledTimes(0); // 「取消」分支绝不允许顺带写盘
    expect(draftDiscard).toHaveBeenCalledTimes(0);
  });

  it('重复请求同一标签是幂等的（不会把询问框叠加成多次决策）', () => {
    const id = newTab({ path: 'D:/tmp/ac59-idem.txt', dirty: true });

    useScratchStore.getState().requestCloseTab(id);
    useScratchStore.getState().requestCloseTab(id);

    expect(useScratchStore.getState().pendingCloseId).toBe(id);
    expect(useScratchStore.getState().tabs).toHaveLength(1);
  });

  it('「取消」分支 = clearCloseRequest()：询问框收起、标签与内容原样保留', () => {
    const id = newTab({ path: 'D:/tmp/ac59-cancel.txt', dirty: true });
    useScratchStore.getState().requestCloseTab(id);

    useScratchStore.getState().clearCloseRequest();

    expect(useScratchStore.getState().pendingCloseId).toBeNull();
    expect(useScratchStore.getState().tabs.map((tab) => tab.id)).toEqual([id]);
    expect(tabOf(id).dirty).toBe(true);
    expect(writeText).toHaveBeenCalledTimes(0);
  });

  it('「放弃」分支 = draftDiscard + closeTab（对齐 ScratchWorkspace.tsx:152-159）：标签消失且草稿被清理', () => {
    const id = newTab({ path: 'D:/tmp/ac59-discard.txt', dirty: true });
    useScratchStore.getState().requestCloseTab(id);

    // 工作区的「放弃修改」处理器顺序：先显式丢弃草稿，再关闭（否则脏标记会让草稿留存、下次又被还原）
    void scratchService.draftDiscard(id);
    useScratchStore.getState().closeTab(id);

    expect(draftDiscard).toHaveBeenCalledWith(id);
    expect(useScratchStore.getState().tabs).toHaveLength(0);
    expect(useScratchStore.getState().pendingCloseId).toBeNull(); // 关闭时顺带收起询问框
    expect(writeText).toHaveBeenCalledTimes(0);
  });

  it('「保存」分支成功 = savePane 后 closeTab（对齐 ScratchWorkspace.tsx:135-149）：写回一次且标签消失', async () => {
    const id = newTab({ path: 'D:/tmp/ac59-save.txt', dirty: true });
    registerContent(id, () => 'saved');
    useScratchStore.getState().requestCloseTab(id);

    const saved = await useScratchStore.getState().savePane(id);
    if (saved) useScratchStore.getState().closeTab(id);

    expect(saved).toBe(true);
    expect(writeText).toHaveBeenCalledTimes(1);
    expect(useScratchStore.getState().tabs).toHaveLength(0);
    expect(useScratchStore.getState().pendingCloseId).toBeNull();
  });

  it('「保存」分支失败（草稿形态取消原生对话框）= clearCloseRequest：标签保持打开且仍为脏', async () => {
    const id = newTab({ path: null, title: '未命名', dirty: true });
    registerContent(id, () => 'draft');
    useScratchStore.getState().requestCloseTab(id);
    pickSaveTarget.mockResolvedValue(null); // 用户在原生对话框里点了取消

    const saved = await useScratchStore.getState().savePane(id);
    if (!saved) useScratchStore.getState().clearCloseRequest();

    expect(saved).toBe(false);
    expect(pickSaveTarget).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledTimes(0);
    expect(useScratchStore.getState().pendingCloseId).toBeNull();
    expect(useScratchStore.getState().tabs.map((tab) => tab.id)).toEqual([id]);
    expect(tabOf(id).dirty).toBe(true);
  });

  it('干净标签：requestCloseTab 直接关闭（不弹询问，顺带丢弃草稿）', () => {
    const id = newTab({ path: 'D:/tmp/ac59-clean.txt' });
    useScratchStore.getState().requestCloseTab(id);

    expect(useScratchStore.getState().pendingCloseId).toBeNull();
    expect(useScratchStore.getState().tabs).toHaveLength(0);
    expect(draftDiscard).toHaveBeenCalledWith(id);
  });

  it('关闭请求指向的标签被关闭时，pendingCloseId 必须一起清空（不留悬空引用）', () => {
    const id = newTab({ path: 'D:/tmp/ac59-stale.txt', dirty: true });
    useScratchStore.getState().requestCloseTab(id);

    useScratchStore.getState().closeTab(id);

    expect(useScratchStore.getState().pendingCloseId).toBeNull();
  });
});

describe('AC-65 openOrFocus：同路径只聚焦不新建不覆盖 + §4.3 四条不变量', () => {
  it('同路径再次打开：返回已有标签 id、tabs 不增、激活项切到它、正文不被覆盖（不投递初始正文）', () => {
    const path = 'D:/tmp/ac65-dedup.txt';
    const existingId = newTab({ path, title: 'ac65-dedup.txt' });
    // 先造一个"另一条激活项"，验证 openOrFocus 会把它抢回来
    const otherId = newTab({ path: null });

    const result = useScratchStore.getState().openOrFocus(readItemOf(path, '磁盘上的旧内容'));

    expect(result).toBe(existingId);
    expect(useScratchStore.getState().tabs).toHaveLength(2);
    expect(useScratchStore.getState().activeTabId).toBe(existingId);
    // AC-65 ④：不覆盖内容 —— 没有为已有标签投递"初始正文"（投递即会在挂载时覆盖 CM6 正文）
    expect(takeInitialText(existingId)).toBeNull();
    expect(takeInitialText(otherId)).toBeNull();
  });

  it('新路径：新建标签、把正文登记为初始正文、并成为激活项', () => {
    const path = 'D:/tmp/ac65-new.txt';

    const id = useScratchStore.getState().openOrFocus(readItemOf(path, '新内容'));

    expect(id).not.toBeNull();
    const tab = tabOf(id as string);
    expect(tab.path).toBe(path);
    expect(tab.title).toBe('ac65-new.txt');
    expect(tab.dirty).toBe(false);
    expect(useScratchStore.getState().activeTabId).toBe(id);
    expect(takeInitialText(id as string)).toBe('新内容'); // 取走即删（只交付一次）
    expect(takeInitialText(id as string)).toBeNull();
  });

  it('失败条目：不新建标签，只落 lastError（文案取自 Python，不含完整路径）', () => {
    const before = useScratchStore.getState().tabs.length;

    const result = useScratchStore.getState().openOrFocus({
      path: 'D:/tmp/ac65-binary.bin',
      ok: false,
      reason: 'binary',
      message: 'ac65-binary.bin 不是文本文件',
    });

    expect(result).toBeNull();
    expect(useScratchStore.getState().tabs).toHaveLength(before);
    expect(useScratchStore.getState().lastError).toBe('ac65-binary.bin 不是文本文件');
  });

  it('不变量②③：activeTabId 恒命中某个标签 id；关闭非激活标签不改变激活项', () => {
    const first = newTab({ path: 'D:/tmp/inv-a.txt' });
    const second = newTab({ path: 'D:/tmp/inv-b.txt' });
    const third = newTab({ path: 'D:/tmp/inv-c.txt' });
    useScratchStore.getState().setActiveTab(second);

    const active = useScratchStore.getState().activeTabId;
    expect(useScratchStore.getState().tabs.some((tab) => tab.id === active)).toBe(true);

    useScratchStore.getState().closeTab(first); // 非激活项

    expect(useScratchStore.getState().activeTabId).toBe(second);
    expect(useScratchStore.getState().tabs.map((tab) => tab.id)).toEqual([second, third]);

    // 清空间 store：不变量②的"空 ⇒ null"一侧
    useScratchStore.getState().closeTab(second);
    useScratchStore.getState().closeTab(third);
    expect(useScratchStore.getState().tabs).toHaveLength(0);
    expect(useScratchStore.getState().activeTabId).toBeNull();
  });
});
