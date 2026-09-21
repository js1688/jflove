/**
 * Mermaid 图块组件用例（v1.5.0，Web 端）
 *
 * 用受控的假渲染器验证组件行为（渲染调度的缓存/超时见 `mermaid-render.test.ts`）：
 *   - AC-13：进入视口才渲染（懒加载）——未交叉时不得触发渲染；
 *   - AC-21：渲染中是固定宽高比的骨架，不是空白；
 *   - AC-12：语法错误只降级本图块，展示原因 + 行号 + 源码；
 *   - AC-14：引擎不可用降级为源码卡（复制 + 重试），不抛异常；
 *   - AC-11：主题变化触发重新渲染（颜色烘焙进 SVG，必须重画）；
 *   - AC-10：缩放被限制在 50%–400%；
 *   - AC-8 安全：插入画布的 SVG 必须经过清洗（script/事件属性/危险协议被移除）。
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const renderMermaidMock = vi.hoisted(() => vi.fn());

vi.mock('../../src/utils/mermaid/renderer', () => ({
  renderMermaid: renderMermaidMock,
  cacheKeyOf: vi.fn(async () => 'test-key'),
}));

import { MermaidBlock } from '../../src/components/markdown/MermaidBlock';
import type { MermaidResult } from '../../src/utils/mermaid/types';

const OK_RESULT: MermaidResult = {
  ok: true,
  svg: '<svg viewBox="0 0 320 180"><rect width="320" height="180"/></svg>',
  width: 320,
  height: 180,
  elapsed: 12,
  kind: 'Flowchart',
};

/** 受控 IntersectionObserver：测试里手动触发"进入视口" */
let triggerIntersect: ((entries: Array<{ isIntersecting: boolean }>) => void) | null = null;
let disconnectCount = 0;

class FakeIntersectionObserver {
  root = null;
  rootMargin = '200px 0px';
  thresholds = [0];

  constructor(cb: (entries: Array<{ isIntersecting: boolean }>) => void) {
    triggerIntersect = cb;
  }

  observe(): void {}
  unobserve(): void {}

  disconnect(): void {
    disconnectCount += 1;
    triggerIntersect = null;
  }

  takeRecords(): [] {
    return [];
  }
}

function enterViewport(): void {
  act(() => {
    triggerIntersect?.([{ isIntersecting: true }]);
  });
}

describe('MermaidBlock', () => {
  beforeEach(() => {
    renderMermaidMock.mockReset();
    renderMermaidMock.mockResolvedValue(OK_RESULT);
    disconnectCount = 0;
    triggerIntersect = null;
    vi.stubGlobal('IntersectionObserver', FakeIntersectionObserver);
    document.documentElement.classList.remove('dark');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.documentElement.classList.remove('dark');
  });

  it('AC-13：未进入视口时不渲染（懒加载）', async () => {
    render(<MermaidBlock code={'flowchart LR\n  A --> B'} index={0} />);

    expect(renderMermaidMock).not.toHaveBeenCalled();
    expect(screen.getByText('Flowchart')).toBeInTheDocument();
    expect(disconnectCount).toBe(0);
  });

  it('AC-21：渲染中显示固定宽高比的骨架占位与提示', async () => {
    let resolveRender: ((r: MermaidResult) => void) | undefined;
    renderMermaidMock.mockImplementation(
      () => new Promise<MermaidResult>((resolve) => (resolveRender = resolve)),
    );

    const { container } = render(<MermaidBlock code={'flowchart LR\n  A --> B'} index={0} />);
    enterViewport();

    await waitFor(() => expect(renderMermaidMock).toHaveBeenCalledTimes(1));
    const skeleton = container.querySelector('[aria-busy="true"]');
    expect(skeleton).not.toBeNull();
    expect(skeleton).toHaveAttribute('aria-label', '图表 1 渲染中');
    expect(screen.getByText('渲染图表中…')).toBeInTheDocument();
    // 骨架用 aspect-ratio 固定高度，成图后不跳动
    expect((skeleton as HTMLElement).style.aspectRatio).not.toBe('');

    await act(async () => {
      resolveRender?.(OK_RESULT);
    });
    await waitFor(() => expect(screen.getByText('语法校验通过')).toBeInTheDocument());
  });

  it('进入视口后渲染成功：插入经清洗的 SVG 并显示耗时与缩放控件', async () => {
    const { container } = render(<MermaidBlock code={'flowchart LR\n  A --> B'} index={1} />);
    enterViewport();

    await waitFor(() => expect(screen.getByText('语法校验通过')).toBeInTheDocument());
    expect(renderMermaidMock).toHaveBeenCalledWith('flowchart LR\n  A --> B', 'light');
    const canvas = container.querySelector('.mmd-canvas');
    expect(canvas?.innerHTML).toContain('<svg');
    expect(screen.getByText('渲染 12 ms')).toBeInTheDocument();
    expect(screen.getByText('图表 2')).toBeInTheDocument();
  });

  it('AC-8 安全：插入画布的 SVG 已清洗（script / 事件属性 / 危险协议被移除）', async () => {
    renderMermaidMock.mockResolvedValue({
      ...OK_RESULT,
      svg: '<svg viewBox="0 0 10 10"><script>alert(1)</script><a href="javascript:alert(2)"><text>x</text></a></svg>',
    });

    const { container } = render(<MermaidBlock code={'flowchart LR\n  A --> B'} index={0} />);
    enterViewport();

    await waitFor(() => expect(screen.getByText('语法校验通过')).toBeInTheDocument());
    const html = container.querySelector('.mmd-canvas')?.innerHTML ?? '';
    expect(html).not.toContain('<script');
    expect(html).not.toContain('javascript:');
  });

  it('AC-12：语法错误降级为本图块的错误卡（含原因与行号），其余内容不受影响', async () => {
    renderMermaidMock.mockResolvedValue({
      ok: false,
      error: { reason: 'parse', line: 3, message: 'Parse error on line 3' },
    });

    const { container } = render(
      <div>
        <p>文档其余内容</p>
        <MermaidBlock code={'flowchart LR\n  A -->'} index={2} onJumpToSource={() => {}} />
      </div>,
    );
    enterViewport();

    await waitFor(() => expect(screen.getByText('语法错误')).toBeInTheDocument());
    expect(screen.getByText('第 3 行：Parse error on line 3')).toBeInTheDocument();
    expect(container.querySelector('.mmd-error pre')?.textContent).toContain('flowchart LR');
    // 文档其余内容照常
    expect(screen.getByText('文档其余内容')).toBeInTheDocument();
    // 提供了跳到源码入口
    expect(screen.getByLabelText('跳到源码')).toBeInTheDocument();
    expect(screen.getByLabelText('重新渲染')).toBeInTheDocument();
  });

  it('AC-14：引擎不可用降级为源码卡，不报错也不白屏', async () => {
    renderMermaidMock.mockResolvedValue({
      ok: false,
      error: { reason: 'engine', line: 0, message: '渲染引擎资源加载失败' },
    });

    const { container } = render(<MermaidBlock code={'sequenceDiagram\n  A->>B: hi'} index={0} />);
    enterViewport();

    await waitFor(() => expect(screen.getByText('渲染引擎不可用')).toBeInTheDocument());
    expect(screen.getByText('渲染引擎资源加载失败')).toBeInTheDocument();
    expect(container.querySelector('.mmd-error pre')?.textContent).toContain('sequenceDiagram');
    expect(screen.getByLabelText('复制源码')).toBeInTheDocument();
  });

  it('渲染器 Promise 被拒绝时也降级为错误卡（不冒泡到文档级）', async () => {
    renderMermaidMock.mockRejectedValue(new Error('boom'));

    render(<MermaidBlock code={'flowchart LR\n  A --> B'} index={0} />);
    enterViewport();

    await waitFor(() => expect(screen.getByText('渲染失败')).toBeInTheDocument());
  });

  it('AC-10：缩放限制在 50%–400% 之间', async () => {
    render(<MermaidBlock code={'flowchart LR\n  A --> B'} index={0} />);
    enterViewport();
    await waitFor(() => expect(screen.getByText('100%')).toBeInTheDocument());

    const zoomIn = screen.getByLabelText('放大');
    for (let i = 0; i < 40; i += 1) fireEvent.click(zoomIn);
    expect(screen.getByText('400%')).toBeInTheDocument();

    const zoomOut = screen.getByLabelText('缩小');
    for (let i = 0; i < 60; i += 1) fireEvent.click(zoomOut);
    expect(screen.getByText('50%')).toBeInTheDocument();
  });

  it('AC-11：主题切换后重新渲染（不是靠 CSS 换色）', async () => {
    render(<MermaidBlock code={'flowchart LR\n  A --> B'} index={0} />);
    enterViewport();
    await waitFor(() => expect(screen.getByText('语法校验通过')).toBeInTheDocument());
    expect(renderMermaidMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      document.documentElement.classList.add('dark');
      window.dispatchEvent(new Event('jflove:theme-changed'));
      // 让 MutationObserver 回调、重渲染 Promise 与随后的状态提交都在 act 内结算
      await Promise.resolve();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(renderMermaidMock).toHaveBeenCalledTimes(2);
    expect(renderMermaidMock).toHaveBeenLastCalledWith('flowchart LR\n  A --> B', 'dark');
  });
});
