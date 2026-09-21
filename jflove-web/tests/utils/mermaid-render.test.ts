/**
 * Mermaid 渲染调度行为用例（v1.5.0，Web 端）
 *
 * 覆盖 AC-11 / AC-12 / AC-14 / AC-21 / AC-22 里"不靠肉眼"的部分：
 *   - 同源码 + 同主题 → 命中缓存（二次显示零成本，渲染引擎只被调用一次）；
 *   - 换主题 → **必须重新渲染**（颜色烘焙进 SVG，返回旧图会导致暗底留亮图）；
 *   - 失败结果不缓存（允许用户点"重新渲染"重试）；
 *   - 语法错误 → 解析出行号与原因（1-based）；
 *   - 超时 → 降级为 timeout 结果，不无限等待骨架。
 *
 * 做法：往 `window.mermaid` 装一个可观测的假引擎（`loadMermaid()` 会直接复用它），
 * 因此不需要真的下载 3.3MB 离线包。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { clearMermaidCache } from '../../src/utils/mermaid/cache';
import { cacheKeyOf, renderMermaid, __testing } from '../../src/utils/mermaid/renderer';
import type { MermaidApi } from '../../src/types/mermaid';

interface FakeCalls {
  initialize: unknown[];
  parse: string[];
  render: string[];
}

function installFakeEngine(
  overrides: Partial<MermaidApi> = {},
): { calls: FakeCalls; api: MermaidApi } {
  const calls: FakeCalls = { initialize: [], parse: [], render: [] };

  const api: MermaidApi = {
    version: '11.16.0',
    initialize: (config) => {
      calls.initialize.push(config);
    },
    parse: async (text) => {
      calls.parse.push(text);
      return true;
    },
    render: async (_id, text) => {
      calls.render.push(text);
      return { svg: `<svg viewBox="0 0 320 180"><text>${text.length}</text></svg>` };
    },
    ...overrides,
  };

  window.mermaid = api;
  return { calls, api };
}

/** 一个标准的语法错误对象（mermaid 的行号是 0-based） */
function parseError(firstLine: number): Error {
  const err = new Error('Parse error on line 3\n更多内部细节不应展示给用户');
  (err as Error & { hash?: unknown }).hash = { loc: { first_line: firstLine } };
  return err;
}

describe('mermaid 渲染调度', () => {
  beforeEach(() => {
    clearMermaidCache();
    __testing.reset();
    sessionStorage.clear();
  });

  afterEach(() => {
    delete window.mermaid;
    vi.restoreAllMocks();
    vi.useRealTimers();
    clearMermaidCache();
    __testing.reset();
  });

  it('同源码同主题命中缓存：引擎只渲染一次', async () => {
    const { calls } = installFakeEngine();
    const code = 'flowchart LR\n  A --> B';

    const first = await renderMermaid(code, 'light');
    const second = await renderMermaid(code, 'light');

    expect(first.ok).toBe(true);
    expect(second).toEqual(first);
    expect(calls.parse).toEqual([code]);
    expect(calls.render).toEqual([code]); // 第二次没有再次渲染
  });

  it('换主题必须重新渲染，并用新主题重新 initialize', async () => {
    const { calls } = installFakeEngine();
    const code = 'flowchart LR\n  A --> B';

    await renderMermaid(code, 'light');
    await renderMermaid(code, 'dark');

    expect(calls.render).toHaveLength(2);
    expect(calls.initialize).toHaveLength(2);
    const [lightCfg, darkCfg] = calls.initialize as [
      { themeVariables: { darkMode: boolean } },
      { themeVariables: { darkMode: boolean } },
    ];
    expect(lightCfg.themeVariables.darkMode).toBe(false);
    expect(darkCfg.themeVariables.darkMode).toBe(true);
  });

  it('源码首尾空白不影响缓存命中（trim 后同键）', async () => {
    const { calls } = installFakeEngine();
    const code = 'flowchart LR\n  A --> B';

    await renderMermaid(`\n\n${code}  \n`, 'light');
    await renderMermaid(code, 'light');

    expect(calls.render).toHaveLength(1);
  });

  it('空图表内容直接降级为 parse 失败，不触碰引擎', async () => {
    const { calls } = installFakeEngine();
    const result = await renderMermaid('   \n  ', 'light');

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.reason).toBe('parse');
      expect(result.error.message).toBe('图表内容为空');
    }
    expect(calls.render).toHaveLength(0);
  });

  it('语法错误：给出行号（1-based）与首行原因，且只影响本图', async () => {
    installFakeEngine({
      parse: async () => {
        throw parseError(2);
      },
    });

    const result = await renderMermaid('flowchart LR\n  A -->\n', 'light');

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.reason).toBe('parse');
      expect(result.error.line).toBe(3);
      expect(result.error.message).toBe('Parse error on line 3'); // 只取首行
      expect(result.error.message).not.toContain('内部细节');
    }
  });

  it('无位置信息的错误：行号为 0（前端展示"无法定位行号"），原因照常给出', async () => {
    installFakeEngine({
      render: async () => {
        throw new Error('boom');
      },
    });

    const result = await renderMermaid('flowchart LR\n  A --> B', 'light');
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.line).toBe(0);
      expect(result.error.reason).toBe('runtime');
      expect(result.error.message).toBe('boom');
    }
  });

  it('失败结果不进缓存，允许重试', async () => {
    let attempts = 0;
    installFakeEngine({
      parse: async () => {
        attempts += 1;
        if (attempts === 1) throw parseError(0);
        return true;
      },
    });

    const code = 'flowchart LR\n  A --> B';
    const failed = await renderMermaid(code, 'light');
    const retried = await renderMermaid(code, 'light');

    expect(failed.ok).toBe(false);
    expect(retried.ok).toBe(true); // 第二次真的重新渲染了
    expect(attempts).toBe(2);
  });

  it('渲染超时降级为 timeout 结果（不会无限等骨架）', async () => {
    vi.useFakeTimers();

    // 用一个"已进入渲染"的信号代替 sleep：解析到它时，withTimeout 的计时器
    // 一定已经建立，之后再推进假时钟就不会出现"计时器还没建就推进"的竞态
    // （在并行跑全量用例、事件循环繁忙时曾经偶发过）。
    let markParseStarted: (() => void) | null = null;
    const parseStarted = new Promise<void>((resolve) => {
      markParseStarted = resolve;
    });

    installFakeEngine({
      parse: () => {
        markParseStarted?.();
        return new Promise(() => {}); // 永不 resolve
      },
    });

    const pending = renderMermaid('flowchart LR\n  A --> B', 'light');
    await parseStarted;
    await vi.advanceTimersByTimeAsync(__testing.RENDER_TIMEOUT_MS + 50);
    const result = await pending;

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.reason).toBe('timeout');
      expect(result.error.message).toContain('5 秒');
    }
  }, 15000);

  it('缓存键长度稳定且与主题相关（同一键可跨会话命中 sessionStorage）', async () => {
    const light = await cacheKeyOf('flowchart LR\n  A --> B', 'light');
    const dark = await cacheKeyOf('flowchart LR\n  A --> B', 'dark');
    expect(light.length).toBe(dark.length);
    expect(light).not.toBe(dark);
  });
});
