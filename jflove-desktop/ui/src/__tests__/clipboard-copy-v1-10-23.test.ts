/**
 * 剪贴板写入策略（v1.10.23）
 *
 * ## 背景（台账 T-3）
 *
 * `copyText` 旧实现是「先写 Python 桥，失败再试浏览器 API」。
 * 在 QtWebEngine 里浏览器剪贴板 API 是**永久 pending** 的（`readText`/`writeText` 都如此），
 * 所以那条兜底路径一旦走到就必然白等 `CLIPBOARD_TIMEOUT_MS`（500ms）。
 *
 * 判据（与 `readClipboardText` 同构）：
 * 1. **桥就绪 ⇒ 先且只用桥**，成功即返回；
 * 2. 桥不可用 ⇒ 才尝试浏览器 `writeText`（带超时）；
 * 3. 两者都不行 ⇒ 退 `textarea + execCommand` 兜底；
 * 4. **桥就绪且成功时，`navigator.clipboard.writeText` 一次都不该被调用**。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const bridgeWrites: string[] = [];
let bridgeReady = false;
let bridgeShouldFail = false;

vi.mock('../utils/desktop-bridge', () => ({
  isBridgeReady: () => bridgeReady,
  callBridge: async (_service: string, _method: string, params: { text?: string }) => {
    if (bridgeShouldFail) throw new Error('bridge down');
    bridgeWrites.push(String(params?.text ?? ''));
    return { ok: true };
  },
}));

describe('copyText 策略（v1.10.23）', () => {
  let mod: typeof import('../utils/clipboard');

  beforeEach(async () => {
    bridgeWrites.length = 0;
    bridgeReady = false;
    bridgeShouldFail = false;
    vi.resetModules();
    mod = await import('../utils/clipboard');
    mod.__resetClipboardProbeForTest();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('桥就绪时只走桥，浏览器 writeText 一次都不被调用', async () => {
    bridgeReady = true;
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });

    await expect(mod.copyText('HELLO')).resolves.toBe(true);
    expect(bridgeWrites).toEqual(['HELLO']);
    expect(writeText).not.toHaveBeenCalled();
  });

  it('桥不可用时才回退浏览器 writeText', async () => {
    bridgeReady = false;
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });

    await expect(mod.copyText('WORLD')).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith('WORLD');
    expect(bridgeWrites).toHaveLength(0);
  });

  it('桥写失败时仍会用浏览器 API 兜一次', async () => {
    bridgeReady = true;
    bridgeShouldFail = true;
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });

    await expect(mod.copyText('FALLBACK')).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith('FALLBACK');
  });

  it('空文本直接返回 false（不触发任何写入路径）', async () => {
    bridgeReady = true;
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });

    await expect(mod.copyText('')).resolves.toBe(false);
    expect(bridgeWrites).toHaveLength(0);
    expect(writeText).not.toHaveBeenCalled();
  });

  it('两条路都失败时退到 execCommand 兜底', async () => {
    bridgeReady = false;
    bridgeShouldFail = true;
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
    const execCommand = vi.fn(() => true);
    Object.defineProperty(document, 'execCommand', { configurable: true, value: execCommand });

    await expect(mod.copyText('LAST_RESORT')).resolves.toBe(true);
    expect(execCommand).toHaveBeenCalledWith('copy');
  });
});
