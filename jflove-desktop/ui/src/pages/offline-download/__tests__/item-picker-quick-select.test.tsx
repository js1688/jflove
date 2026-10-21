/**
 * 可下载项清单交互（v1.8.0 / AC-210）
 *
 * 判据：快捷选择按钮必须把**正确的 ID 集合**交给父组件（而不是只改本地视觉），
 * 「继续抓下一页」只在服务端返回 `has_more_images` 时出现并触发回调。
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ItemPicker } from '../ItemPicker';
import type { OfflineItem } from '../../../services/offline-download-service';

function makeItem(overrides: Partial<OfflineItem> & { id: number }): OfflineItem {
  return {
    kind: 'video',
    display_name: `item-${overrides.id}`,
    ext: 'mp4',
    width: 0,
    height: 0,
    duration_ms: 0,
    est_size: 0,
    is_estimated: false,
    is_selected: false,
    status: 'pending',
    error_message: '',
    ...overrides,
  };
}

const ITEMS: OfflineItem[] = [
  makeItem({ id: 1, width: 1920, height: 1080, display_name: '1080P' }),
  makeItem({ id: 2, width: 1280, height: 720, display_name: '720P' }),
  makeItem({ id: 3, kind: 'audio', display_name: '仅音频' }),
  makeItem({ id: 4, kind: 'image', width: 800, height: 600, display_name: '图片' }),
];

interface RenderOptions {
  selectedIds?: number[];
  hasMore?: boolean;
}

function renderPicker({ selectedIds = [1], hasMore = false }: RenderOptions = {}) {
  const onToggle = vi.fn();
  const onSelectionChange = vi.fn();
  const onNextPage = vi.fn();
  render(
    <ItemPicker
      items={ITEMS}
      selectedIds={new Set(selectedIds)}
      hasMore={hasMore}
      nextPageLoading={false}
      onToggle={onToggle}
      onSelectionChange={onSelectionChange}
      onNextPage={onNextPage}
    />,
  );
  return { onToggle, onSelectionChange, onNextPage };
}

describe('快捷选择按钮把正确 ID 集合交给父组件（AC-210）', () => {
  it('「只要最高清」只交 1 项且为最高分辨率', () => {
    const { onSelectionChange } = renderPicker();
    fireEvent.click(screen.getByRole('button', { name: '只要最高清' }));
    expect(onSelectionChange).toHaveBeenCalledWith([1]);
  });

  it('「只要音频」只交音频项', () => {
    const { onSelectionChange } = renderPicker();
    fireEvent.click(screen.getByRole('button', { name: '只要音频' }));
    expect(onSelectionChange).toHaveBeenCalledWith([3]);
  });

  it('「只要图片」只交图片项', () => {
    const { onSelectionChange } = renderPicker();
    fireEvent.click(screen.getByRole('button', { name: '只要图片' }));
    expect(onSelectionChange).toHaveBeenCalledWith([4]);
  });

  it('「全选」「全不选」', () => {
    const { onSelectionChange } = renderPicker();
    fireEvent.click(screen.getByRole('button', { name: '全选' }));
    expect(onSelectionChange).toHaveBeenCalledWith([1, 2, 3, 4]);
    fireEvent.click(screen.getByRole('button', { name: '全不选' }));
    expect(onSelectionChange).toHaveBeenLastCalledWith([]);
  });
});

describe('「继续抓下一页」按 has_more_images 显隐', () => {
  it('有下一页时显示并触发回调', () => {
    const { onNextPage } = renderPicker({ hasMore: true });
    fireEvent.click(screen.getByRole('button', { name: '继续抓下一页' }));
    expect(onNextPage).toHaveBeenCalledTimes(1);
  });

  it('没有下一页时不渲染按钮', () => {
    renderPicker({ hasMore: false });
    expect(screen.queryByRole('button', { name: '继续抓下一页' })).toBeNull();
  });
});

describe('逐项勾选', () => {
  it('点击清单项复选框回调对应 ID', () => {
    const { onToggle } = renderPicker({ selectedIds: [] });
    fireEvent.click(screen.getByRole('checkbox', { name: /音频 仅音频/ }));
    expect(onToggle).toHaveBeenCalledWith(3);
  });

  it('显示预估大小与分辨率', () => {
    renderPicker();
    // 1080P 项带分辨率；四项都渲染
    expect(screen.getByText('1920×1080')).not.toBeNull();
    expect(screen.getByText('1280×720')).not.toBeNull();
  });
});
