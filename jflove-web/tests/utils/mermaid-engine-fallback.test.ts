/**
 * 渲染引擎不可用时的降级用例（AC-14）
 *
 * 用 `vi.mock` 让离线包加载失败，验证 renderer **不抛异常**，
 * 而是返回 `reason: 'engine'` 的失败结果 —— 上层据此展示"源码卡"，
 * 做到"不报错、不白屏"（需求 2.3 渲染不可用降级 / §3.4 稳定性）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../src/utils/mermaid/loader', () => ({
  loadMermaid: vi.fn(),
  isMermaidLoaded: vi.fn(() => false),
  EXPECTED_MERMAID_VERSION: '11.16.0',
}));

import { clearMermaidCache } from '../../src/utils/mermaid/cache';
import { loadMermaid } from '../../src/utils/mermaid/loader';
import { renderMermaid, __testing } from '../../src/utils/mermaid/renderer';
import type { MermaidApi, MermaidResult } from '../../src/types/mermaid';

const loadMermaidMock = vi.mocked(loadMermaid);

/** 一个最小可用的假引擎（引擎恢复后使用） */
const healthyEngine: MermaidApi = {
  initialize: () => {},
  parse: async () => true,
  render: async () => ({ svg: '<svg viewBox="0 0 10 10"><circle r="1"/></svg>' }),
};

describe('渲染引擎不可用（离线包缺失 / 加载失败）', () => {
  beforeEach(() => {
    clearMermaidCache();
    __testing.reset(); // 清掉"已初始化主题"，保证 initialize 分支每次都会被走到
    loadMermaidMock.mockReset();
    loadMermaidMock.mockRejectedValue(new Error('渲染引擎资源加载失败'));
  });

  it('返回 engine 失败而不是抛异常（供上层降级为源码卡）', async () => {
    const result: MermaidResult = await renderMermaid('flowchart LR\n  A --> B', 'light');

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.reason).toBe('engine');
      expect(result.error.message).toContain('渲染引擎');
      expect(result.error.line).toBe(0);
    }
  });

  it('引擎不可用的失败结果也不进缓存（引擎恢复后能立即渲染）', async () => {
    const first = await renderMermaid('flowchart LR\n  A --> B', 'light');
    expect(first.ok).toBe(false);

    // 引擎恢复可用：同源码同主题必须真的重新渲染（说明上次失败没被缓存）
    loadMermaidMock.mockReset();
    loadMermaidMock.mockResolvedValue(healthyEngine);

    const second = await renderMermaid('flowchart LR\n  A --> B', 'light');
    expect(second.ok).toBe(true);
    if (second.ok) {
      expect(second.svg).toContain('<circle');
    }
  });

  it('引擎初始化抛异常时也降级为 engine 失败', async () => {
    loadMermaidMock.mockReset();
    loadMermaidMock.mockResolvedValue({
      ...healthyEngine,
      initialize: () => {
        throw new Error('Unsupported color format: "var(--brand-50)"');
      },
    });

    const result = await renderMermaid('flowchart LR\n  A --> B', 'light');
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.reason).toBe('engine');
      expect(result.error.message).toContain('渲染引擎初始化失败');
    }
  });
});
