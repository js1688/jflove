/**
 * 窗格 store「保存 / 自动暂存」语义负向对照 —— Phase 6（v1.6.0）
 *
 * ## 被测对象
 *
 * `stores/scratch-store.ts` 的**唯一落盘通路**：
 *
 * ```
 * markDirty(id, true) → scheduleFlush(id)                     // scratch-store.ts:588-597 / 261-270
 *   └─ AUTO_SAVE_DEBOUNCE_MS（1500，scratch-store.ts:19-21 同值常量）后
 *      flushPane(id)                                          // scratch-store.ts:277-319
 *        ① 总是 scratchService.draftSave(...)                  // 草稿库（两种形态的唯一/共同落点）
 *        ② pane.path !== null && !pane.readOnly 才 writeText   // 用户文件（AC-40 / AC-19）
 *        ③ wroteUserFile && 期间无新编辑 ⇒ dirty=false          // AC-54 的"不重写"前提
 * flushPendingSaves()                                          // scratch-store.ts:706-710
 *       = 立刻触发所有待写窗格（窗格关闭对话框的「保存」与退出前落盘都走它）
 * savePane(id, { forceDialog })                                // scratch-store.ts 的显式保存（BL-33）
 *       = `Ctrl+S` / `Ctrl+Shift+S` 的落点：已有路径直接写回；草稿形态先 pickSaveTarget
 *         （取消 ⇒ 不写、仍为脏）；只读 / 未挂载 ⇒ 拒绝并给出可读错误
 * ```
 *
 * ## 判据出处（需求原文）
 *
 * - AC-37：「已有路径的文档保存不再询问：已保存文档（目标 `p`）内容改为 `y` → 按 `Ctrl+S` →
 *   ① 全程不出现任何要求选择位置或文件名的对话框；② `p` 内容等于 `y`；③ 所在目录的文件数量与
 *   文件名集合不变（无副本、无新文件）」⇒ 本例断言：**0 次对话框调用 + 恰好 1 次写回同一路径**。
 * - AC-40：「自动保存只对已有路径的文档生效 ② 草稿形态（从未保存）窗格输入 `c` → 等待 ≥ 2 倍防抖
 *   间隔 → 用户文件系统上没有任何文件被写入 `c`」⇒ 本例断言：3000 ms 后 `writeText` 次数为 0。
 * - AC-54：「输入内容后不保存 → 等待设计文档规定的自动暂存间隔 + 余量后，本机草稿存在且内容一致；
 *   随后内容不变再等一个周期 → 草稿文件 mtime 不变（未重复写）」⇒ 到期判据 = 1500 ms；
 *   1499 ms 不触发、1500 ms 触发；干净（不脏）时到期不产生写入。
 * - AC-19 / AC-52：「`.diff` / `.patch` 以只读方式打开…且保存不可用」/「`> 5 MB` 只读打开…
 *   保存不可用」⇒ 本例断言：只读窗格在任何保存通路上 `writeText` 次数为 0。
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
    // 只替身化"会碰文件系统/原生对话框"的四个方法，其余（含 toDraftPaneMeta）保持真实实现
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
  useScratchStore,
} from '../scratch-store';
import type { ScratchPane } from '../../types/scratch';

const draftSave = vi.mocked(scratchService.draftSave);
const writeText = vi.mocked(scratchService.writeText);
const reportDirty = vi.mocked(scratchService.reportDirty);
const pickSaveTarget = vi.mocked(scratchService.pickSaveTarget);
const openDialog = vi.mocked(scratchService.openDialog);

/** 本用例创建过的窗格（afterEach 统一 close，清掉 store 的模块级旁路表与计时器） */
const createdPaneIds: string[] = [];
/** 正文读取器注销函数（与窗格一一对应） */
const accessorReleases: Array<() => void> = [];

/** 建窗格并登记到清理清单 */
function createPane(init: Partial<ScratchPane>): string {
  const id = useScratchStore.getState().createPane(init);
  createdPaneIds.push(id);
  return id;
}

/**
 * 注册正文读取器（对齐 `ScratchEditorHost` 挂载时的做法）。
 *
 * `flushPane` 读不到读取器就**直接返回**（`scratch-store.ts:280-281`），所以每个要落盘的窗格
 * 必须先注册 —— 这也让"未挂载窗格只有元数据、没有正文"这条边界在本文件里可复现。
 */
function registerContent(paneId: string, text: () => string): void {
  accessorReleases.push(
    registerScratchContentAccessor(paneId, () => ({
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

/** 取窗格当前状态 */
function paneOf(id: string): ScratchPane {
  const pane = useScratchStore.getState().panes.find((item) => item.id === id);
  if (!pane) throw new Error(`窗格不存在：${id}`);
  return pane;
}

beforeEach(() => {
  vi.useFakeTimers();
  // 模块级 mock 的默认实现（`restoreMocks` 只恢复 spy，这里显式重置更稳）
  draftSave.mockResolvedValue({ ok: true, at: 1 });
  vi.mocked(scratchService.draftDiscard).mockResolvedValue({ ok: true });
  writeText.mockResolvedValue({ ok: true, size: 1, mtime: 1, sha256: 'x' });
  reportDirty.mockResolvedValue({ ok: true });
  pickSaveTarget.mockResolvedValue(null); // 取消对话框
  openDialog.mockResolvedValue({ cancelled: true, path: null });
  useScratchStore.setState({ panes: [], activePaneId: null, zCounter: 0, lastError: null });
});

afterEach(() => {
  accessorReleases.splice(0).forEach((release) => release());
  createdPaneIds.splice(0).forEach((id) => useScratchStore.getState().closePane(id));
  vi.useRealTimers();
});

describe('AC-54 到期判据 = AUTO_SAVE_DEBOUNCE_MS（1500 ms）', () => {
  it('常量本身必须是 1500（需求 D21 裁决：debounce 1.5 s，判据以 1.5 s 为准）', () => {
    expect(AUTO_SAVE_DEBOUNCE_MS).toBe(1500);
  });

  it('推进 1499 ms 不触发；再推进 1 ms（=1500）触发一次草稿写入与一次用户文件写回', async () => {
    const id = createPane({ path: 'D:/tmp/ac54.txt' });
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
    const id = createPane({ path: 'D:/tmp/ac54-clean.txt' });
    registerContent(id, () => 'a');
    useScratchStore.getState().markDirty(id, true);

    await advance(AUTO_SAVE_DEBOUNCE_MS);
    expect(draftSave).toHaveBeenCalledTimes(1);
    // 写回成功且期间无新编辑 ⇒ 脏标记落下（AC-54 的下一个周期"未重复写"的前提）
    expect(paneOf(id).dirty).toBe(false);

    await advance(AUTO_SAVE_DEBOUNCE_MS * 2);
    expect(draftSave).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledTimes(1);

    // 干净态直接触发批量落盘（窗格关闭对话框的「保存」/ 退出前落盘）也不得写入
    await useScratchStore.getState().flushPendingSaves();
    expect(draftSave).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledTimes(1);
  });

  it('只读窗格到期后：草稿库照写（内容不丢），用户文件 0 次写入、脏标记不清（AC-19 同层守门）', async () => {
    const id = createPane({ path: 'D:/tmp/ac19.diff', readOnly: true });
    registerContent(id, () => 'diff 内容');
    useScratchStore.getState().markDirty(id, true);

    await advance(AUTO_SAVE_DEBOUNCE_MS);
    expect(draftSave).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledTimes(0);
    expect(paneOf(id).dirty).toBe(true);
  });

  it('只读窗格走"立刻落盘"通路（flushPendingSaves）同样 0 次写入、脏标记不清（主动保存路径的同层守门）', async () => {
    // 与 M-11（批量保存的**筛选**漏了 readOnly）区分：这里测的是**写回动作本身**的守门
    // —— 即便某条通路把只读窗格送进了落盘流程，`flushPane` 也不得调用 writeText（AC-19 / AC-52）。
    const id = createPane({ path: 'D:/tmp/ac19-explicit.diff', readOnly: true });
    registerContent(id, () => 'diff 内容');
    useScratchStore.getState().markDirty(id, true);

    await useScratchStore.getState().flushPendingSaves();

    expect(writeText).toHaveBeenCalledTimes(0);
    expect(draftSave).toHaveBeenCalledTimes(1); // 草稿库照写：内容不丢
    expect(paneOf(id).dirty).toBe(true);
  });
});

describe('AC-37 已有路径保存：不询问 + 恰好一次写回同一路径（AC-40 ① 同源）', () => {
  it('防抖到期写回原路径一次，全程 0 次对话框（openDialog / pickSaveTarget）', async () => {
    const id = createPane({ path: 'D:/tmp/ac37.txt' });
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
    const id = createPane({ path: 'D:/tmp/ac40-1.txt' });
    registerContent(id, () => 'b');
    useScratchStore.getState().markDirty(id, true);

    await advance(AUTO_SAVE_DEBOUNCE_MS * 2);

    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText.mock.calls[0]?.[0]?.text).toBe('b');
    expect(paneOf(id).dirty).toBe(false);
  });

  it('批量落盘（flushPendingSaves）对已有路径的待写窗格同样只写一次、不询问', async () => {
    const id = createPane({ path: 'D:/tmp/ac37-flush.txt' });
    registerContent(id, () => 'y');
    useScratchStore.getState().markDirty(id, true);

    await useScratchStore.getState().flushPendingSaves();

    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText.mock.calls[0]?.[0]?.path).toBe('D:/tmp/ac37-flush.txt');
    expect(openDialog).toHaveBeenCalledTimes(0);
    expect(pickSaveTarget).toHaveBeenCalledTimes(0);
    expect(paneOf(id).dirty).toBe(false);
  });
});

describe('AC-40 ② 草稿形态（path === null）：自动暂存只进草稿库，用户文件零写入', () => {
  it('推进 3000 ms（2 × 防抖）：writeText 次数为 0，draftSave 恰好 1 次', async () => {
    const id = createPane({ path: null });
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
    const id = createPane({ path: null });
    registerContent(id, () => 'c');
    useScratchStore.getState().markDirty(id, true);

    await advance(AUTO_SAVE_DEBOUNCE_MS * 3);

    expect(paneOf(id).path).toBeNull();
    expect(paneOf(id).dirty).toBe(true);
    expect(writeText).toHaveBeenCalledTimes(0);
    // 自动暂存不得弹原生对话框（弹窗只属于用户显式触发的保存 / 另存为）
    expect(openDialog).toHaveBeenCalledTimes(0);
    expect(pickSaveTarget).toHaveBeenCalledTimes(0);
  });

  it('批量落盘也不会把草稿形态静默写盘（D17 红线）', async () => {
    const id = createPane({ path: null });
    registerContent(id, () => 'c');
    useScratchStore.getState().markDirty(id, true);

    await useScratchStore.getState().flushPendingSaves();

    expect(writeText).toHaveBeenCalledTimes(0);
    expect(paneOf(id).dirty).toBe(true);
  });
});

describe('BL-33 显式保存（savePane）：AC-36 / AC-37 的 store 层判据 + 审查 M4 回归', () => {
  it('已有路径：0 次对话框、恰好 1 次写回原路径、dirty 落 false 且返回 true', async () => {
    const id = createPane({ path: 'D:/tmp/bugfix-save.txt' });
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
    expect(paneOf(id).dirty).toBe(false);
  });

  it('草稿形态：必须先询问路径；取消 ⇒ 0 次写入、仍为脏、path 仍为 null、返回 false', async () => {
    const id = createPane({ path: null });
    registerContent(id, () => 'x');
    useScratchStore.getState().markDirty(id, true);
    pickSaveTarget.mockResolvedValue(null); // 用户取消对话框

    const ok = await useScratchStore.getState().savePane(id);

    expect(pickSaveTarget).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledTimes(0);
    expect(paneOf(id).path).toBeNull();
    expect(paneOf(id).dirty).toBe(true);
    expect(ok).toBe(false);
  });

  it('草稿形态选到路径：写到选定路径、回填 path/title、dirty 落 false', async () => {
    const id = createPane({ path: null });
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
    expect(paneOf(id).path).toBe('D:/tmp/新建文档.txt');
    expect(paneOf(id).title).toBe('新建文档.txt');
    expect(paneOf(id).dirty).toBe(false);
  });

  it('只读窗格：拒绝保存（0 次写入 / 0 次对话框、仍为脏）并给出可读错误（AC-19 / AC-52 守门）', async () => {
    const id = createPane({ path: 'D:/tmp/bugfix-readonly.diff', readOnly: true });
    registerContent(id, () => 'diff');
    useScratchStore.getState().markDirty(id, true);

    const ok = await useScratchStore.getState().savePane(id);

    expect(ok).toBe(false);
    expect(writeText).toHaveBeenCalledTimes(0);
    expect(pickSaveTarget).toHaveBeenCalledTimes(0);
    expect(paneOf(id).dirty).toBe(true);
    expect(useScratchStore.getState().lastError).toContain('只读');
  });

  it('未挂载窗格（无正文读取器）：拒绝保存且提示编辑器未就绪（不得写空内容覆盖文件）', async () => {
    const id = createPane({ path: 'D:/tmp/bugfix-nomount.txt' });
    useScratchStore.getState().markDirty(id, true);

    const ok = await useScratchStore.getState().savePane(id);

    expect(ok).toBe(false);
    expect(writeText).toHaveBeenCalledTimes(0);
    expect(useScratchStore.getState().lastError).toContain('编辑器尚未就绪');
  });

  it('审查 M4 回归：保存失败不得吞掉已排队的自动暂存（失败出口要重新排上防抖）', async () => {
    const id = createPane({ path: 'D:/tmp/bugfix-m4.txt' });
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
