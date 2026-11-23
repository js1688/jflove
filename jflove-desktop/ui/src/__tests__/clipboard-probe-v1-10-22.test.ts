/**
 * 剪贴板读取策略单测（v1.10.22）
 *
 * ## 事故
 *
 * QtWebEngine 里 `navigator.clipboard.readText()` **既不 resolve 也不 reject**
 * （实测 3 秒仍挂着）。旧实现每次粘贴都先等它满 `CLIPBOARD_TIMEOUT_MS`（500ms）
 * 才回退到 Python 桥 —— 用户感受到的是「每次粘贴都卡半秒」。
 *
 * 判据：
 * 1. 第一次读：等满超时后回退到桥；
 * 2. **第二次读：不再等超时，直接走桥**（总耗时大幅下降）；
 * 3. 浏览器 API 正常返回时优先用它，不额外走桥；
 * 4. 剪贴板确实为空（API 返回 ''）时不该被误判成"API 不可用"。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// 桥调用用假实现替换：记录调用次数并可注入返回值
const bridgeCalls: Array<{ service: string; method: string }> = [];
let bridgeResult: { text?: string } = { text: 'FROM_BRIDGE' };

let bridgeReady = false; // 默认按"普通浏览器"（无桥）处理，覆盖浏览器 API 分支

vi.mock('../utils/desktop-bridge', () => ({
  isBridgeReady: () => bridgeReady,
  callBridge: async (service: string, method: string) => {
    bridgeCalls.push({ service, method });
    return bridgeResult;
  },
}));

describe('readClipboardText 策略（v1.10.22）', () => {
  let mod: typeof import('../utils/clipboard');

  beforeEach(async () => {
    bridgeCalls.length = 0;
    bridgeResult = { text: 'FROM_BRIDGE' };
    bridgeReady = false;
    vi.resetModules();
    mod = await import('../utils/clipboard');
    // 模块级状态（"浏览器剪贴板是否可用"）会跨用例残留，必须显式复位
    mod.__resetClipboardProbeForTest();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('浏览器 API 永挂时：第一次等超时后回退桥，第二次直接走桥（不再白等）', async () => {
    // 模拟 QtWebEngine 的行为：readText 返回一个永不 settle 的 Promise
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { readText: () => new Promise<string>(() => {}) },
    });

    const first = await mod.readClipboardText();
    expect(first).toBe('FROM_BRIDGE');
    expect(bridgeCalls).toHaveLength(1);

    // 第二次：探测已标记"不可用"，应当立刻返回桥结果（不会再有 500ms 等待）
    const startedAt = Date.now();
    const second = await mod.readClipboardText();
    const elapsed = Date.now() - startedAt;
    expect(second).toBe('FROM_BRIDGE');
    expect(bridgeCalls).toHaveLength(2);
    expect(elapsed).toBeLessThan(100); // 远小于 500ms 超时
  }, 10000);

  it('桌面端（桥就绪）优先走桥：首次粘贴也不白等浏览器 API 的 500ms', async () => {
    bridgeReady = true;
    // 即使浏览器 API 会永挂，也不该被调用到
    const readText = vi.fn(() => new Promise<string>(() => {}));
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { readText } });

    const startedAt = Date.now();
    const text = await mod.readClipboardText();
    const elapsed = Date.now() - startedAt;

    expect(text).toBe('FROM_BRIDGE');
    expect(readText).not.toHaveBeenCalled();
    expect(elapsed).toBeLessThan(100); // 没有 500ms 白等
  }, 10000);

  it('浏览器 API 可用时优先用它，不走桥', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { readText: async () => 'FROM_BROWSER' },
    });
    const text = await mod.readClipboardText();
    expect(text).toBe('FROM_BROWSER');
    expect(bridgeCalls).toHaveLength(0);
  });

  it('剪贴板确实为空（API 返回空串）时不该被当成 API 不可用', async () => {
    const readText = vi.fn(async () => '');
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { readText } });
    expect(await mod.readClipboardText()).toBe('');
    // 空串是可信答案 ⇒ 不应再去问桥
    expect(bridgeCalls).toHaveLength(0);
    // 第二次仍是问 API（没有被误标成不可用）
    await mod.readClipboardText();
    expect(readText).toHaveBeenCalledTimes(2);
  });

  it('没有 navigator.clipboard 时直接走桥', async () => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
    expect(await mod.readClipboardText()).toBe('FROM_BRIDGE');
    expect(bridgeCalls).toHaveLength(1);
  });

  it('桥也失败时返回空串而不是抛错', async () => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
    bridgeResult = undefined as unknown as { text?: string };
    await expect(mod.readClipboardText()).resolves.toBe('');
  });
});
