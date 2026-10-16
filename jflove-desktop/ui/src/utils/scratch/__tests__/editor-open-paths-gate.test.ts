/**
 * 「外部唤起 → 登录门 → 打开并揭示」接线单测 —— Phase 6（v1.6.1 · T1a）
 *
 * 判据载体：设计 v1.6.1 §10.1 点名的 `utils/scratch/__tests__/editor-open-paths-gate.test.ts`
 * （`文档记录/设计文档/v1.6.1.md:788`：「`editor-open-paths-gate.test.ts`（mock `onBridgeEvent`
 * + gate 翻转）」），对应 **AC-64**（唤起 → 登录 → 自动打开）、**AC-65**（同路径去重聚焦）、
 * **AC-47 ②**（登录后不依赖服务端）。
 *
 * ## 被测的真实链路（**不替换被测对象，只替换桥的最底层**）
 *
 * ```
 * bridgeMock.emit('editor.openPaths', {paths, source})            ← 假装 Python 侧推事件
 *   └─ desktop-bridge.onBridgeEvent 的注册表（唯一替身点）
 *      └─ scratch-service.onEditorOpenPaths → parseOpenPathsPayload   ← 真实实现（不可信输入校验）
 *         └─ open-paths-queue 模块顶层订阅 → enqueueOpenPaths          ← 真实实现
 *            └─ flushOpenPaths：**首行判门**                            ← 真实实现（AC-64 的核心）
 *               ├─ 门 = currentEditorGate（editor-open-paths.ts:110）   ← 真实实现
 *               └─ 消费者 = openPathsAndReveal                          ← 真实实现
 *                  ├─ router.navigate('/editor')
 *                  ├─ scratchService.detectAndRead（替身）
 *                  └─ scratchStore.openOrFocus（真实实现，AC-65）
 * ```
 *
 * 判据出处：
 * - 设计 `:487-533`（§4.4 队列侧扩展 + 接线 + §4.4 判据表四行）；
 * - 设计 `:405-425`（§4.1 准入单一真相：门必须由 `currentEditorGate` 提供）、
 *   `:971-983`（§14.3 负向对照：**AC-64 注入"丢弃挂起的唤起请求" ⇒ 本文件必须失败；
 *   AC-65 注入"同路径走新开标签分支" ⇒ 本文件必须失败**）；
 * - 需求 `文档记录/需求文档/v1.6.1.md:469`（AC-64 ①~④ + `[负向对照]`）、
 *   `:470`（AC-65 ①~④）、`:488`（AC-47 ②）。
 *
 * ## 与设计 §4.4「为什么判门必须放在 flushOpenPaths 而不是消费者里」的对应
 *
 * `flushOpenPaths` 一旦投递就会 `rememberDelivered()`，而 `enqueueOpenPaths` 对 `delivered`
 * 命中的路径**拒绝入队** ⇒ "先投递、消费者发现没登录再退回"会让路径**永久丢失**。
 * 本文件用「未登录时显式 flush 两次 → 登录 → 仍能打开」这条链路把该口径锁死：
 * 只要判门被删掉 / 被挪到消费者里（投递之后），这条用例**必然变红**。
 *
 * ## 分层与安全（AGENTS.md §9.6 / §9.14）
 *
 * - 桥**最底层**（`utils/desktop-bridge.ts`）被替身化 ⇒ 不发任何真实网络请求、不连 QWebChannel；
 *   其余模块（队列 / 接线 / store / 解析）全部走真实实现；
 * - 路由表被替身化成内存路由（不把整张真实路由表拉进单测），`/editor` 用 `*` 兜底；
 * - fixture 里**没有** token / session_key；路径只用固定的假路径字符串，无真实文件访问。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * 桥替身：唯一被替换的是「Python 推事件的注册表」。
 *
 * 与 `src/__tests__/scratch-save-decision.test.tsx:48-72` 同一手法（照抄，不另发明）。
 */
const bridgeMock = vi.hoisted(() => {
  const listeners = new Map<string, Set<(payload: unknown) => void>>();
  /**
   * 经桥发出的调用记录（**只记模块 / 方法名**，不记路径与内容 —— AGENTS.md §9.14）。
   *
   * 用途：AC-45 ③ 的「未勾选」侧要证明**打开通路不查勾选清单** ——
   * 勾选只是应用内偏好（`components/scratch/FileAssocSettings.tsx:9-11`：
   * 「文件被系统唤起时…无论是否勾选都会用 JFLove 打开」），任何 `file_assoc.*`
   * 调用出现在这条链上都是"把未勾选扩展名拦下来"的实现苗头。
   */
  const calls: Array<{ module: string; method: string }> = [];
  return {
    bridgeCalls: calls,
    emit(topic: string, payload: unknown): void {
      listeners.get(topic)?.forEach((handler) => handler(payload));
    },
    listenerCount(topic: string): number {
      return listeners.get(topic)?.size ?? 0;
    },
    onBridgeEvent(topic: string, handler: (payload: unknown) => void): () => void {
      let set = listeners.get(topic);
      if (!set) {
        set = new Set();
        listeners.set(topic, set);
      }
      set.add(handler);
      return () => {
        set?.delete(handler);
      };
    },
    callBridge: async (mod: string, method: string): Promise<Record<string, never>> => {
      calls.push({ module: mod, method });
      return {};
    },
    callShell: async (): Promise<boolean> => true,
    isBridgeReady: (): boolean => true,
    getChannelObject: (): null => null,
    whenBridgeReady: async (): Promise<void> => undefined,
  };
});

// ⚠ 路径口径：本文件位于 `src/utils/scratch/__tests__/` ⇒ 到 `src/utils/` 是 `../../`。
vi.mock('../../desktop-bridge', () => bridgeMock);

// 路由替身：只给一个内存路由（`/editor` 不单独登记，由 `*` 兜底匹配），
// 避免把整张真实路由表（含全部页面与它们的桥调用）拖进本判据。
vi.mock('../../../config/routes', async () => {
  const { createMemoryRouter } = await import('react-router');
  return {
    router: createMemoryRouter([{ path: '*', Component: () => null }], { initialEntries: ['/'] }),
  };
});

/**
 * 桥调用服务：只替身化「会碰文件系统 / 远端」的方法（与
 * `stores/__tests__/scratch-store-save.test.ts:67-82` 同一手法）。
 *
 * `onEditorOpenPaths` **保持真实实现** —— 它只是把 handler 挂到被替身的
 * `desktop-bridge.onBridgeEvent` 注册表上，因此"事件 → 解析 → 入队 → 判门 → 投递"
 * 整条链路都是被测的真实代码。
 */
vi.mock('../../../services/scratch-service', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/scratch-service')>();
  return {
    ...actual,
    scratchService: {
      ...actual.scratchService,
      detectAndRead: vi.fn(),
      writeText: vi.fn(),
      draftSave: vi.fn(),
      draftDiscard: vi.fn(),
      reportDirty: vi.fn(),
    },
  };
});

import { router } from '../../../config/routes';
import { scratchService } from '../../../services/scratch-service';
import { useAuthStore } from '../../../stores/auth-store';
import {
  registerScratchContentAccessor,
  takeInitialText,
  useScratchStore,
} from '../../../stores/scratch-store';
import type { ReadItemOk } from '../../../types/scratch';
import { setAuthSnapshot } from '../../session';
// 副作用 import：`editor-open-paths.ts:138` 在模块顶层自动装机一次（幂等）
import '../editor-open-paths';
import {
  MAX_PENDING_OPEN_PATHS,
  droppedOpenPaths,
  enqueueOpenPaths,
  flushOpenPaths,
  pendingOpenPaths,
  resetOpenPathsQueue,
} from '../open-paths-queue';

const detectAndRead = vi.mocked(scratchService.detectAndRead);
const writeText = vi.mocked(scratchService.writeText);
const draftSave = vi.mocked(scratchService.draftSave);
const draftDiscard = vi.mocked(scratchService.draftDiscard);
const reportDirty = vi.mocked(scratchService.reportDirty);

/** 正文读取器注销函数（本文件只登记"编辑器已挂载"这一前提） */
const accessorReleases: Array<() => void> = [];

/** 造一条 `detectAndRead` 成功条目（字段名逐字对齐 `types/scratch.ts:100-111`） */
function readItemOf(path: string, text = '磁盘上的内容'): ReadItemOk {
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

/**
 * 把登录态翻成「已登录 + 未过期」——门从 false 翻到 true 的**唯一**事件（AC-64 ②）。
 *
 * `setAuthSnapshot` 同步渲染态缓存，`useAuthStore.setState` 触发 `useSyncExternalStore` /
 * `subscribe` 的通知（接线里的登录订阅就是靠它兑现挂起请求）。
 */
function login(): void {
  setAuthSnapshot({ loggedIn: true, expiresAt: null });
  useAuthStore.setState({ isLoggedIn: true });
}

/** 让 `openPathsAndReveal` 的 `await detectAndRead(...)` 异步链走完（纯微任务，无定时器） */
async function settle(ticks = 12): Promise<void> {
  for (let i = 0; i < ticks; i += 1) await Promise.resolve();
}

beforeEach(() => {
  // 桥调用记录清零（只是新增判据的观测面：既有断言一条都没动）
  bridgeMock.bridgeCalls.length = 0;

  // 模块级 mock 的默认实现（`restoreMocks` 对 `vi.fn()` 更稳的做法是显式重置）
  detectAndRead.mockImplementation(async (paths: string[]) => ({
    items: paths.map((path) => readItemOf(path)),
  }));
  writeText.mockResolvedValue({ ok: true, size: 1, mtime: 1, sha256: 'x' });
  draftSave.mockResolvedValue({ ok: true, at: 1 });
  draftDiscard.mockResolvedValue({ ok: true });
  reportDirty.mockResolvedValue({ ok: true });

  // 顺序有讲究：先清队列（`subscribe` 在 pending 为 0 时直接返回），再摆未登录态
  resetOpenPathsQueue();
  useScratchStore.setState({ tabs: [], activeTabId: null, lastError: null, pendingCloseId: null });
  setAuthSnapshot({ loggedIn: false, expiresAt: null });
  useAuthStore.setState({ isLoggedIn: false });
});

afterEach(() => {
  accessorReleases.splice(0).forEach((release) => release());
  useScratchStore
    .getState()
    .tabs.slice()
    .forEach((tab) => useScratchStore.getState().closeTab(tab.id));
  resetOpenPathsQueue();
  setAuthSnapshot({ loggedIn: false, expiresAt: null });
  useAuthStore.setState({ isLoggedIn: false });
});

describe('AC-64 前置：接线的装机时机与消费者存在性', () => {
  it('AC-64 ① 模块 import 即装机：桥存在 `editor.openPaths` 消费者（未登录时也要有人接住，否则启动期事件被丢）', () => {
    // 「消费者早于登录存在」是设计 §4.4 三件必做事的第 ② 件（`:508` 注释）
    expect(
      bridgeMock.listenerCount('editor.openPaths'),
      '消费者未注册：启动期（早于 UI）推来的路径会被 desktop-bridge 直接丢弃',
    ).toBeGreaterThan(0);
  });
});

describe('AC-64 ① 未登录经唤起 ⇒ 入队挂起且零远端', () => {
  it('AC-64 ① 未登录收到 `editor.openPaths` ⇒ 入队挂起、零 detectAndRead、零标签页、零 router.navigate', () => {
    const navigate = vi.spyOn(router, 'navigate');
    const path = 'D:/tmp/ac64-挂起.txt';

    bridgeMock.emit('editor.openPaths', { paths: [path], source: 'cli' });

    expect(pendingOpenPaths(), '未登录必须挂起而不是丢弃（AC-64 ③：登录后自动打开）').toBe(1);
    expect(useScratchStore.getState().tabs, 'AC-64 ①：此时没有任何标签页').toHaveLength(0);
    expect(detectAndRead, 'AC-64 ①：未登录零远端读取').not.toHaveBeenCalled();
    expect(navigate, 'AC-64 ①：未登录不得切菜单').not.toHaveBeenCalled();
    expect(router.state.location.pathname, '位置必须原地不动').toBe('/');
    expect(useScratchStore.getState().lastError, '未登录不是错误：不得报错').toBeNull();
  });

  it('AC-64 [负向对照] 桥的另一端按不可信输入处理：非法 / 空 / 空白路径一律不入队', () => {
    bridgeMock.emit('editor.openPaths', { paths: 'not-an-array', source: 'cli' });
    bridgeMock.emit('editor.openPaths', { paths: ['', '   '], source: 'cli' });
    bridgeMock.emit('editor.openPaths', null);

    expect(pendingOpenPaths()).toBe(0);
    expect(detectAndRead).not.toHaveBeenCalled();
  });
});

describe('AC-64 ②③ 登录态翻转 ⇒ 自动切菜单 + 按入队序打开', () => {
  it("AC-64 ②③ 登录成功 ⇒ navigate('/editor') + flushOpenPaths()，且严格按入队序打开、激活最后一条", async () => {
    const navigate = vi.spyOn(router, 'navigate');
    const first = 'D:/tmp/ac64-甲.txt';
    const second = 'D:/tmp/ac64-乙.txt';

    // 两次独立事件（cli 与单实例转发）——顺序必须保持命令行顺序
    bridgeMock.emit('editor.openPaths', { paths: [first], source: 'cli' });
    bridgeMock.emit('editor.openPaths', { paths: [second], source: 'single_instance' });
    expect(pendingOpenPaths()).toBe(2);
    expect(detectAndRead).not.toHaveBeenCalled();

    login();
    await settle();
    await vi.waitFor(() => {
      expect(router.state.location.pathname, 'AC-64 ②：左侧菜单必须自动切到文本编辑器').toBe(
        '/editor',
      );
    });

    expect(navigate).toHaveBeenCalled();
    expect(detectAndRead, '同一批只读一次').toHaveBeenCalledTimes(1);
    expect(detectAndRead.mock.calls[0]?.[0], '顺序 = 入队顺序，绝不重排').toEqual([first, second]);
    const tabs = useScratchStore.getState().tabs;
    expect(tabs.map((tab) => tab.path), 'AC-64 ③：两个标签，逐条保序').toEqual([first, second]);
    expect(useScratchStore.getState().activeTabId, 'AC-64 ③：最后一次打开的标签为激活项').toBe(
      tabs[1]?.id,
    );
    expect(pendingOpenPaths(), '兑现后队列清空').toBe(0);
    expect(useScratchStore.getState().lastError).toBeNull();
  });

  it('AC-47 ② / AC-71 已登录时经唤起直接打开：无登录拦截、零服务端校验、零错误', async () => {
    login();
    expect(pendingOpenPaths()).toBe(0);

    bridgeMock.emit('editor.openPaths', { paths: ['D:/tmp/ac71-已登录.txt'], source: 'cli' });
    await settle();

    expect(detectAndRead, '已登录 ⇒ 同步兑现，不经过任何登录门等待').toHaveBeenCalledTimes(1);
    expect(useScratchStore.getState().tabs).toHaveLength(1);
    expect(useScratchStore.getState().lastError).toBeNull();
  });
});

describe('AC-64 [核心判据] 判门在**投递之前**（设计 §4.4 的粗体说明）', () => {
  it('AC-64 未登录时 flush 不得把路径记入 `delivered` ⇒「未登录 flush → 登录 → 仍能打开」', async () => {
    const path = 'D:/tmp/ac64-判门顺序.txt';

    bridgeMock.emit('editor.openPaths', { paths: [path], source: 'cli' });

    // 显式再 flush 两次：模拟"注册消费者时立刻 flush""登录态抖动/登出"等最容易被写坏的路径。
    // 若判门被删掉或挪到消费者里（投递之后），这里就会把路径投出去并记入 delivered。
    expect(flushOpenPaths()).toEqual([]);
    expect(flushOpenPaths('single_instance')).toEqual([]);
    expect(pendingOpenPaths(), '门必须在投递之前：队列不得被消费').toBe(1);
    expect(detectAndRead, '未登录不得投递 ⇒ 也不得读文件').not.toHaveBeenCalled();

    // 仍被"在队内"去重 ⇒ 证明它确实还挂在 pending 里（而不是已被 delivered 吃掉）
    expect(enqueueOpenPaths([path])).toBe(0);
    expect(pendingOpenPaths()).toBe(1);

    login();
    await settle();

    expect(detectAndRead, '登录后必须仍能打开：路径未被 delivered 永久吞掉').toHaveBeenCalledTimes(1);
    expect(detectAndRead.mock.calls[0]?.[0]).toEqual([path]);
    expect(useScratchStore.getState().tabs.map((tab) => tab.path), 'AC-64 ③').toEqual([path]);
    expect(useScratchStore.getState().activeTabId).toBe(useScratchStore.getState().tabs[0]?.id);

    // 此刻才记入 delivered：Python 侧的 1500 ms 重试再推一次不会翻倍（open-paths-queue.ts:120）
    expect(enqueueOpenPaths([path])).toBe(0);
    expect(pendingOpenPaths()).toBe(0);
  });
});

describe('AC-65 同路径去重聚焦（不覆盖未保存改动）', () => {
  it('AC-65 ①②③④ 已有同路径脏标签 ⇒ 只聚焦：tabs 数不变、正文不被磁盘内容覆盖、磁盘文件未被写', async () => {
    login();
    const path = 'D:/tmp/ac65-脏标签.txt';

    // 前置：同一路径已开成标签页，且有**未保存**改动（脏）
    const existingId = useScratchStore.getState().createTab({
      path,
      title: 'ac65-脏标签.txt',
      dirty: true,
    });
    accessorReleases.push(
      registerScratchContentAccessor(existingId, () => ({
        text: '用户未保存的编辑：绝不能被磁盘内容覆盖',
      })),
    );
    const otherId = useScratchStore.getState().createTab({ path: null, title: '另一个标签' });
    expect(useScratchStore.getState().activeTabId, '前置：激活项先离开目标标签').toBe(otherId);

    // 再次经「打开方式」传入同一路径
    bridgeMock.emit('editor.openPaths', { paths: [path], source: 'cli' });
    await settle();

    // 去重发生在 `openOrFocus`（store 层），不是在队列：Python 侧仍会真读一次
    expect(detectAndRead).toHaveBeenCalledTimes(1);

    const tabs = useScratchStore.getState().tabs;
    expect(tabs.map((tab) => tab.id), 'AC-65 ①：标签页数量不增加').toEqual([existingId, otherId]);
    expect(useScratchStore.getState().activeTabId, 'AC-65 ②：激活的是已有标签').toBe(existingId);
    // AC-65 ③ 的机制性证据：同路径分支**不投递初始正文**（投递即会在挂载时覆盖 CM6 正文）
    expect(takeInitialText(existingId), '不得为已有标签投递磁盘正文').toBeNull();
    expect(takeInitialText(otherId)).toBeNull();
    expect(
      tabs.find((tab) => tab.id === existingId)?.dirty,
      '未保存改动仍为脏（未被当成"已从磁盘重载"）',
    ).toBe(true);
    expect(writeText, 'AC-65 ④：去重聚焦不得顺带写盘').not.toHaveBeenCalled();
    expect(useScratchStore.getState().lastError).toBeNull();
  });
});

describe('AC-64 ⑤ 队列上限 MAX_PENDING_OPEN_PATHS = 64', () => {
  it('AC-64 ⑤ 超出上限直接丢弃并计数；登录后恰好交付 64 条且保持入队序', async () => {
    expect(MAX_PENDING_OPEN_PATHS, '上限常量本身即为判据（与 DETECT_AND_READ_MAX_PATHS 同量级）').toBe(
      64,
    );

    const paths = Array.from(
      { length: 66 },
      (_, index) => `D:/tmp/ac64-上限-${String(index).padStart(2, '0')}.txt`,
    );
    bridgeMock.emit('editor.openPaths', { paths, source: 'cli' });

    expect(pendingOpenPaths(), '未登录时队列真实堆积到上限').toBe(64);
    expect(droppedOpenPaths(), '超出的 2 条被丢弃并计数（不阻塞启动）').toBe(2);
    expect(useScratchStore.getState().tabs).toHaveLength(0);
    expect(detectAndRead).not.toHaveBeenCalled();

    // 上限已满：再来一条**新**路径同样丢弃并累加计数
    bridgeMock.emit('editor.openPaths', { paths: ['D:/tmp/ac64-上限-第 67 条.txt'], source: 'cli' });
    expect(droppedOpenPaths()).toBe(3);
    expect(pendingOpenPaths()).toBe(64);

    login();
    await settle();

    expect(detectAndRead, '64 条 = 单批（DETECT_AND_READ_MAX_PATHS 同为 64）').toHaveBeenCalledTimes(1);
    expect(detectAndRead.mock.calls[0]?.[0], '交付顺序 = 入队顺序').toEqual(paths.slice(0, 64));
    expect(useScratchStore.getState().tabs.map((tab) => tab.path)).toEqual(paths.slice(0, 64));
    expect(pendingOpenPaths()).toBe(0);
    expect(droppedOpenPaths(), '丢弃计数不因交付而清零（供调用方提示"部分文件未打开"）').toBe(3);
  });
});

/**
 * ────────────────────────────────────────────────────────────────────────────
 * AC-45 ③（就地改写版）「对**未勾选**的扩展名经同一路径打开时**同样**打开编辑器」
 * ────────────────────────────────────────────────────────────────────────────
 *
 * 判据原文：需求 `文档记录/需求文档/v1.6.1.md:487` ③ ——「对**未勾选**的扩展名（如 `.md`）
 * 经同一路径打开时**同样**打开编辑器（**显式选择即意图**）」；
 * 设计映射：`文档记录/设计文档/v1.6.1.md:811`（AC-45 → §4.4 已登录 / 未登录两条路径）。
 *
 * ## 与既有用例的关系（**引用，不重写**）
 *
 * - 机制层「打开产出 `viewMode='edit'`」已由
 *   `components/scratch/__tests__/scratch-editor-panel-render.test.tsx:310-317` 覆盖
 *   （那里**直接调** `openOrFocus`）；本段补的是**目前无单测的那一侧**：未勾选的扩展名经
 *   `editor.openPaths`（系统层「打开方式 → JFLove」）走完整条真实链路后，仍落成**激活的编辑标签页**；
 * - 「应用内新建走另一条路」的机制层对照见同文件 `:329-332`（`createTab` 默认 `viewMode='edit'`），
 *   本段只补它的**链路归属**（不入 openPaths 队列、不产生桥读）。
 *
 * ## 为什么断言打在「标签出现 + 激活项 + 视图模式」而不是「函数被调用过」
 *
 * 这条链最危险的静默失败是：**没抛错、没提示，只是什么都没打开**
 * （扩展名被白名单过滤掉 / `openOrFocus` 走了拒绝分支只写 `lastError`）。
 * 因此判据落在「标签页真的出现且是激活项」与「视图模式真的是 `edit`」这两处可观测产物上。
 */
describe('AC-45 ③ 未勾选扩展名（`.md`）经「打开方式」同样打开编辑器', () => {
  /** `.md` 的磁盘正文（含中文：编码问题只在非 ASCII 内容上暴露 —— 测试纪律 §4） */
  const mdText = '# 未勾选的 Markdown\n\n经「打开方式 → JFLove」进来的中文内容。\n';

  it('AC-45 ③ 未勾选的 `.md` 经 `editor.openPaths` ⇒ 真读一次 + 落成激活标签页 + `viewMode` 仍为 `edit`', async () => {
    login();

    // 前置：先把位置挪离 `/editor`，否则「已切到编辑器」会因上一条用例的导航而恒真
    await router.navigate('/');
    expect(router.state.location.pathname, '前置：位置必须真的离开编辑器').toBe('/');

    const mdPath = 'D:/tmp/ac45-未勾选-文档.md';
    // 替身只做「Python 侧真读」的等价物：`.md` 回 `language:'markdown'`。
    // 依据 `src/services/editor_service.py:108-126`：`detect_and_read` **没有**扩展名白名单，
    // 逐条独立处理、单条失败进 `ok:false`。这里让替身接受 `.md` 是**还原真实能力**，
    // 不是把期望值改小（`open-paths-queue.ts` 与 `openOrFocus` 都不做扩展名判定）。
    detectAndRead.mockImplementationOnce(async (paths: string[]) => ({
      items: paths.map((path) => ({ ...readItemOf(path, mdText), language: 'markdown' })),
    }));

    bridgeMock.emit('editor.openPaths', { paths: [mdPath], source: 'cli' });
    await settle();

    // ① 没被「扩展名白名单 / 关联清单」挡在读取之前：确实走到 detectAndRead，路径原样、只读一次
    expect(detectAndRead, 'AC-45 ③：未勾选也必须走到读文件这一步').toHaveBeenCalledTimes(1);
    expect(detectAndRead.mock.calls[0]?.[0], 'AC-45 ③：路径不得被过滤 / 改写').toEqual([mdPath]);

    // ② 真的落成**标签页**且是**激活项**（不是首页 / 媒体页 / 文件管理页，也不是只写 lastError）
    const tabs = useScratchStore.getState().tabs;
    expect(tabs.map((tab) => tab.path), 'AC-45 ③：必须出现标签页').toEqual([mdPath]);
    expect(useScratchStore.getState().activeTabId, 'AC-45 ③：新标签必须是激活项').toBe(tabs[0]?.id);
    await vi.waitFor(() => {
      expect(router.state.location.pathname, 'AC-45 ③：工作区必须切到编辑器').toBe('/editor');
    });

    // ③ 「显式选择即意图」：`.md` 支持 preview / split（`config/scratch-types.ts:190-198`），
    //    但打开态必须是**编辑** —— 不得被类型表推荐视图或能力探测改写
    expect(tabs[0]?.viewMode, 'AC-45 ③：打开态必须是 edit').toBe('edit');
    expect(tabs[0]?.readOnly, '`.md` 不是强制只读类型（`scratch-types.ts:196`），必须可编辑').toBe(
      false,
    );
    expect(tabs[0]?.language, '语言取 Python 侧的判定结果').toBe('markdown');

    // 「未勾选」不得变成一道门：整条打开通路**零** `file_assoc.*` 调用
    expect(
      bridgeMock.bridgeCalls.filter((call) => call.module === 'file_assoc'),
      '打开通路不得查勾选清单（`FileAssocSettings.tsx:9-11`：无论是否勾选都用 JFLove 打开）',
    ).toEqual([]);
    expect(useScratchStore.getState().lastError, '不得静默失败（只写 lastError 不建标签页）').toBeNull();
  });

  it('AC-45 ③ [负向对照] 同一个 `.md` 在应用内新建（不经「打开方式」）不走本链路', async () => {
    login();

    const id = useScratchStore.getState().createTab({ title: '新建文档.md', path: null });
    await settle();

    expect(detectAndRead, 'AC-45 ③ 对照：应用内新建不借道 detectAndRead').not.toHaveBeenCalled();
    expect(pendingOpenPaths(), 'AC-45 ③ 对照：应用内新建不进「打开方式」队列').toBe(0);
    expect(
      bridgeMock.bridgeCalls.filter((call) => call.module === 'file_assoc'),
      '应用内新建同样不查勾选清单',
    ).toEqual([]);
    const tabs = useScratchStore.getState().tabs;
    expect(tabs.map((tab) => tab.id), 'AC-45 ③ 对照：只有应用内那一个标签').toEqual([id]);
    expect(tabs[0]?.path, '应用内新建是无路径草稿（扩展名只出现在标题里）').toBeNull();
  });
});
