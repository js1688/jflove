/**
 * 「保存」决策层负向对照（应用级）—— Phase 6（v1.6.0）
 *
 * ## 为什么需要这一层
 *
 * `stores/scratch-store.ts` 里只有**防抖自动暂存**（`markDirty` → 1500 ms → `flushPane`）与
 * **批量落盘**（`flushPendingSaves`）。需求 §9.5 要求的是**用户显式触发的保存**：
 *
 * - **AC-36**：「草稿形态首次保存必须由用户指定路径与文件名：新建窗格输入 `x` → 触发保存 →
 *   弹出**原生文件对话框**（可观测：对话框出现）；… **取消对话框** → 磁盘上不产生任何文件、
 *   窗格仍为脏」；
 * - **AC-37**：「已有路径的文档保存不再询问：已保存文档（目标 `p`）内容改为 `y` → 按 `Ctrl+S` →
 *   ① **全程不出现**任何要求选择位置或文件名的对话框；② `p` 内容等于 `y`；③ 目录文件集合不变」。
 *
 * 这两条的触发入口在 `config/shortcuts.ts:161-177` 里是有定义的（`file.save` = `Ctrl+S`、
 * `file.saveAs` = `Ctrl+Shift+S`，描述文字与 AC 原文一致），但**没有任何实现**：
 * 应用级监听只接管 `ScratchPaneLayer.tsx:52-60` 列出的 7 项（不含 `file.save`），
 * CM6 宿主装的 keymap（`ScratchEditorHost.tsx:294-302`）只有内核默认键映射，
 * `scratchService.pickSaveTarget`（`scratch-service.ts:117-120`）**全仓无调用方**。
 * 因此这两条按 `it.fails` 登记（见交付说明的"按需求写但实现不符"清单）。
 *
 * ## 分层声明
 *
 * 只断言 **UI 决策层**（按 `Ctrl+S` 后该调哪个桥方法、几次）；
 * 原生对话框本身与真实文件系统行为由 Python 侧探针覆盖。桥的**最底层**
 * （`utils/desktop-bridge.ts`）在本文件被替身化，因此不会真的去连 QWebChannel。
 */
import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/** 桥替身：可编程地"从 Python 侧推事件"，并记录 JS → Python 的调用 */
const bridgeMock = vi.hoisted(() => {
  const listeners = new Map<string, Set<(payload: unknown) => void>>();
  return {
    emit(topic: string, payload: unknown): void {
      listeners.get(topic)?.forEach((handler) => handler(payload));
    },
    onBridgeEvent(topic: string, handler: (payload: unknown) => void): () => void {
      let set = listeners.get(topic);
      if (!set) {
        set = new Set();
        listeners.set(topic, set);
      }
      set.add(handler);
      return () => {
        listeners.get(topic)?.delete(handler);
      };
    },
    callBridge: async (): Promise<Record<string, never>> => ({}),
    callShell: async (): Promise<boolean> => true,
    isBridgeReady: (): boolean => true,
    getChannelObject: (): null => null,
    whenBridgeReady: async (): Promise<void> => undefined,
    listeners,
  };
});

vi.mock('../../utils/desktop-bridge', () => bridgeMock);

// 路由替身：本文件只关心 App 根组件挂的窗格层 / 退出对话框，
// 不需要真实页面（真实页面在 jsdom 里会发起一堆与本判据无关的桥调用）
vi.mock('../../config/routes', async () => {
  const { createMemoryRouter } = await import('react-router');
  return {
    router: createMemoryRouter([{ path: '*', Component: () => null }], { initialEntries: ['/'] }),
  };
});

import { App } from '../App';
import { scratchService } from '../services/scratch-service';
import { useScratchStore } from '../stores/scratch-store';

/** 在 `window` 上派发一次组合键（AC-36 / AC-37 的原文触发方式是 `Ctrl+S`） */
function pressKey(key: string, options: { ctrlKey?: boolean; altKey?: boolean } = {}): void {
  act(() => {
    window.dispatchEvent(
      new KeyboardEvent('keydown', {
        key,
        code: `Key${key.toUpperCase()}`,
        ctrlKey: Boolean(options.ctrlKey),
        altKey: Boolean(options.altKey),
        bubbles: true,
      }),
    );
  });
}

beforeEach(() => {
  // jsdom 没有窗口管理器：`document.hasFocus()` 恒为 false，而应用级快捷键监听
  // （`ScratchPaneLayer.tsx:282-284`，AC-8 自查）首行就是 `if (!document.hasFocus() || document.hidden) return;`
  // ⇒ 不修这一项就什么都观测不到（连下面的阳性对照也建不出窗格）。
  // 这里把它固定为 true，等价于"窗口已激活"，与真实桌面端一致。
  vi.spyOn(document, 'hasFocus').mockReturnValue(true);
  useScratchStore.setState({ panes: [], activePaneId: null, zCounter: 0, lastError: null });
});

afterEach(() => {
  useScratchStore.setState({ panes: [], activePaneId: null, zCounter: 0, lastError: null });
});

describe('AC-36 / AC-37 显式保存入口（Ctrl+S）', () => {
  it('阳性对照：应用级快捷键监听确实在跑（Ctrl+Alt+N → 新建窗格）', () => {
    // 没有这条对照，下面两条 it.fails 的"没反应"就无法区分是
    // ① 保存键位没接线（真缺陷）还是 ② 整个监听环境不可用（测试自身问题）。
    // `pane.new` 的默认键位 = `Ctrl+Alt+N`（`config/shortcuts.ts:109`），在应用级监听接管名单内。
    render(<App />);
    expect(useScratchStore.getState().panes).toHaveLength(0);

    pressKey('n', { ctrlKey: true, altKey: true });

    expect(useScratchStore.getState().panes).toHaveLength(1);
  });

  it.fails(
    'AC-36【登记】Ctrl+S 对草稿形态窗格必须走「另存为」询问路径（pickSaveTarget 恰好一次）',
    async () => {
      // 需求原文：新建窗格输入 x → 触发保存 → 弹出原生文件对话框
      const pickSaveTarget = vi.spyOn(scratchService, 'pickSaveTarget');
      const writeText = vi.spyOn(scratchService, 'writeText');
      render(<App />);
      act(() => {
        useScratchStore.getState().createPane({ path: null, title: '未命名' });
        useScratchStore.getState().markDirty(useScratchStore.getState().panes[0].id, true);
      });

      pressKey('s', { ctrlKey: true });
      await act(async () => {
        await Promise.resolve();
      });

      // 判据：必须有"询问路径"的另存为流程；不得直接写回原路径（草稿形态本就没有路径）
      expect(pickSaveTarget).toHaveBeenCalledTimes(1);
      expect(writeText).toHaveBeenCalledTimes(0);
    },
  );

  it.fails(
    'AC-36【登记】取消对话框 ⇒ 窗格仍为脏、不产生任何写入调用（pickSaveTarget 被问过 1 次）',
    async () => {
      // 需求原文（AC-36 后半）：「取消对话框 → 磁盘上不产生任何文件、窗格仍为脏」。
      // 先决条件是"确实问了路径"：没有这一步，下面对 dirty / writeText 的断言在
      // "整个保存通路都不存在"的今天会**假装通过**（什么也没发生），所以必须一起断言。
      const pickSaveTarget = vi
        .spyOn(scratchService, 'pickSaveTarget')
        .mockResolvedValue(null); // 用户在原生对话框里点了取消
      const writeText = vi.spyOn(scratchService, 'writeText');
      render(<App />);
      const paneId = useScratchStore.getState().createPane({ path: null, title: '未命名' });
      act(() => {
        useScratchStore.getState().markDirty(paneId, true);
      });

      pressKey('s', { ctrlKey: true });
      await act(async () => {
        await Promise.resolve();
      });

      expect(pickSaveTarget).toHaveBeenCalledTimes(1);
      // 取消 ⇒ 不得写盘、不得改路径，脏标记必须留着（用户还欠一次保存）
      expect(writeText).toHaveBeenCalledTimes(0);
      const pane = useScratchStore.getState().panes.find((item) => item.id === paneId);
      expect(pane?.path).toBeNull();
      expect(pane?.dirty).toBe(true);
    },
  );

  it.fails(
    'AC-37【登记】Ctrl+S 对已有路径窗格必须静默写回恰好一次（0 次对话框）',
    async () => {
      // 需求原文：已保存文档（目标 p）内容改为 y → 按 Ctrl+S → 不出现任何选择位置的对话框
      const writeText = vi.spyOn(scratchService, 'writeText');
      const openDialog = vi.spyOn(scratchService, 'openDialog');
      const pickSaveTarget = vi.spyOn(scratchService, 'pickSaveTarget');
      render(<App />);
      act(() => {
        const id = useScratchStore
          .getState()
          .createPane({ path: 'D:/tmp/ac37-ctrl-s.txt', title: 'ac37-ctrl-s.txt' });
        useScratchStore.getState().markDirty(id, true);
      });

      pressKey('s', { ctrlKey: true });
      await act(async () => {
        await Promise.resolve();
      });

      expect(writeText).toHaveBeenCalledTimes(1);
      expect(writeText.mock.calls[0]?.[0]?.path).toBe('D:/tmp/ac37-ctrl-s.txt');
      expect(openDialog).toHaveBeenCalledTimes(0);
      expect(pickSaveTarget).toHaveBeenCalledTimes(0);
    },
  );
});

describe('M-11 批量保存（"保存并退出" / "全部保存"）必须跳过只读窗格', () => {
  it.fails(
    'M-11【登记】只读脏窗格不得计入"可写回原文件"的数（当前批量筛选只判 dirty && path）',
    async () => {
      render(<App />);
      // 只读窗格（`> 5 MB` 降级只读 / `.diff` 类只读）带有目标路径；
      // `dirty` 经草稿还原路径（`hydrateFromDrafts` 直接采纳索引里的 dirty / read_only）可以同时为真。
      // 判据：只读窗格不得被写入（AC-19 / AC-52"保存不可用"），故批量保存必须把它排除在目标集合外。
      act(() => {
        useScratchStore.getState().createPane({
          path: 'D:/tmp/m11-readonly.log',
          title: 'm11-readonly.log',
          readOnly: true,
          dirty: true,
        });
      });

      // 触发退出握手 → App 挂出退出确认框（`shell.quitRequested`，§6.4 / AC-56）
      act(() => {
        bridgeMock.emit('shell.quitRequested', { timeoutMs: 5000 });
      });

      const saveButton = screen.getByRole('button', { name: '保存并退出' });
      // 需求：可写回原文件的窗格数 = 0（唯一的脏窗格是只读的）⇒ 保存按钮不可用、并给出明确说明
      expect(screen.queryByText(/当前没有可写回原文件的窗格/)).not.toBeNull();
      expect((saveButton as HTMLButtonElement).disabled).toBe(true);
    },
  );
});
