/**
 * 统一空 / 加载 / 错误态组件用例（AC-6）
 *
 * 三端都要有统一组件，且"表现一致"：空状态是插画图标 + 标题 + 说明 + 行动按钮，
 * 加载态是骨架屏（不是转圈），错误态是图标 + 说明 + 重试按钮。
 *
 * 同时锁住 AC-5：这些控件里**不得再出现 emoji 充当图标**（v1.4.2 用 📭 做空状态）。
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { Card, EmptyState, ErrorState, ListSkeleton, Progress, Skeleton } from '../../src/components/ui';

/** emoji 区间（图标类 emoji，用于静态断言"控件里没有 emoji"） */
const EMOJI = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}]/u;

describe('EmptyState（统一空状态）', () => {
  it('渲染标题、说明与行动按钮', () => {
    const onUpload = vi.fn();
    render(
      <EmptyState
        icon="folder"
        title="暂无文件"
        description="把文件拖到这里即可加密上传"
        action={<button onClick={onUpload}>上传文件</button>}
      />,
    );

    expect(screen.getByText('暂无文件')).toBeInTheDocument();
    expect(screen.getByText('把文件拖到这里即可加密上传')).toBeInTheDocument();

    const action = screen.getByRole('button', { name: '上传文件' });
    fireEvent.click(action);
    expect(onUpload).toHaveBeenCalledTimes(1);
  });

  it('用矢量图标而不是 emoji 充当插画（AC-5）', () => {
    const { container } = render(<EmptyState icon="folder" title="暂无数据" />);
    expect(container.querySelector('svg')).not.toBeNull();
    expect(container.querySelector('.empty-art')).not.toBeNull();
    expect(EMOJI.test(container.textContent ?? '')).toBe(false);
  });

  it('不传说明与行动时不渲染多余元素', () => {
    const { container } = render(<EmptyState title="空空如也" />);
    expect(screen.getByText('空空如也')).toBeInTheDocument();
    expect(container.querySelector('button')).toBeNull();
  });
});

describe('ErrorState（统一错误态）', () => {
  it('渲染失败说明与重试按钮，点击触发重试', () => {
    const onRetry = vi.fn();
    render(<ErrorState message="网络不可达" onRetry={onRetry} />);

    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText('加载失败')).toBeInTheDocument();
    expect(screen.getByText('网络不可达')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('不传 onRetry 时不渲染重试按钮（只读场景）', () => {
    render(<ErrorState message="没有权限" />);
    expect(screen.getByText('没有权限')).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('重试按钮文案可定制，且不含 emoji（AC-5）', () => {
    const { container } = render(
      <ErrorState message="失败" onRetry={() => {}} retryLabel="重新加载" />,
    );
    expect(screen.getByRole('button', { name: '重新加载' })).toBeInTheDocument();
    expect(EMOJI.test(container.textContent ?? '')).toBe(false);
  });
});

describe('加载态：骨架屏（AC-6）', () => {
  it('ListSkeleton 按行数渲染骨架块并标记 aria-busy', () => {
    const { container } = render(<ListSkeleton rows={3} />);
    const wrap = container.querySelector('[aria-busy="true"]');
    expect(wrap).not.toBeNull();
    expect(wrap).toHaveAttribute('aria-label', '加载中');
    // 每行 3 个骨架块（图标块 + 两行文字）
    expect(container.querySelectorAll('.skeleton')).toHaveLength(9);
  });

  it('Skeleton 可单独使用且不带文字内容（不干扰无障碍朗读）', () => {
    const { container } = render(<Skeleton className="h-4 w-10" />);
    const el = container.querySelector('.skeleton');
    expect(el).not.toBeNull();
    expect(el).toHaveAttribute('class', expect.stringContaining('h-4'));
    expect(el?.textContent).toBe('');
  });

  it('加载态不使用 emoji（AC-5）', () => {
    const { container } = render(<ListSkeleton rows={2} />);
    expect(EMOJI.test(container.textContent ?? '')).toBe(false);
  });
});

describe('Card / Progress（组件契约）', () => {
  it('Card 支持 hover 悬停态类名（AC-4 层次反馈）', () => {
    const { container } = render(<Card hover>内容</Card>);
    const el = container.firstElementChild as HTMLElement;
    expect(el.className).toContain('card');
    expect(el.className).toContain('card-hover');
    expect(el.textContent).toBe('内容');
  });

  it('Progress 暴露标准 progressbar 语义并带渐变填充类', () => {
    const { container } = render(<Progress value={42} />);
    const bar = screen.getByRole('progressbar');
    expect(bar).toHaveAttribute('aria-valuenow', '42');
    expect(container.querySelector('.progress')).not.toBeNull();
  });
});
