/**
 * v1.6.2 AC-97② 用例：设置页快捷键**分组渲染** + 行内作用域标签
 *
 * 判据（用户原话"至少在配置体验上要知道这个快捷键作用域是哪些"）：
 * - 界面出现**两个分组容器**（`data-shortcut-group="window"` / `"pane"`），组标题为
 *   「应用级」/「文本编辑器级」，各带"任意页面生效"/"编辑面板聚焦"的作用域说明；
 * - 每条**行内**都有作用域标签（不只靠组标题）；
 * - 工具箱 11 条出现在「文本编辑器级」组里。
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { createElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../services/scratch-service', () => ({
  scratchService: {
    shortcutsSave: vi.fn(async () => ({ ok: true })),
    shortcutsLoad: vi.fn(async () => ({ version: 1, bindings: {} })),
  },
  isEditorBridgeReady: () => true,
}));

vi.mock('../../../utils/desktop-bridge', () => ({
  callBridge: vi.fn(async () => ({ ok: true })),
  isBridgeReady: () => true,
  onBridgeEvent: () => () => undefined,
  getChannelObject: () => null,
  callShell: vi.fn(async () => ({ ok: true })),
}));

import { ShortcutsSettingsDialog } from '../ShortcutsSettingsDialog';
import { DEFAULT_BINDINGS } from '../../../config/shortcuts';
import { useShortcutStore } from '../../../stores/shortcut-store';

beforeEach(() => {
  useShortcutStore.setState({
    overrides: {},
    bindings: { ...DEFAULT_BINDINGS },
    conflicts: [],
    crossScopeNotes: [],
    lastError: null,
    loaded: true,
    loading: false,
    saving: false,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function renderDialog() {
  const { container } = render(
    createElement(ShortcutsSettingsDialog, { open: true, onClose: () => undefined }),
  );
  return container;
}

describe('AC-97② 设置页两组分组渲染', () => {
  it('两个分组容器：应用级（任意页面生效）/ 文本编辑器级（编辑面板聚焦）', () => {
    const container = renderDialog();
    const windowGroup = container.querySelector('[data-shortcut-group="window"]');
    const paneGroup = container.querySelector('[data-shortcut-group="pane"]');
    expect(windowGroup, '缺少「应用级」分组').not.toBeNull();
    expect(paneGroup, '缺少「文本编辑器级」分组').not.toBeNull();

    expect(windowGroup?.textContent ?? '').toContain('应用级');
    expect(windowGroup?.textContent ?? '').toContain('任意页面生效');
    expect(paneGroup?.textContent ?? '').toContain('文本编辑器级');
    expect(paneGroup?.textContent ?? '').toContain('编辑面板聚焦');
  });

  it('工具箱 11 条落在「文本编辑器级」组里，且每行都有作用域标签', () => {
    const container = renderDialog();
    const paneGroup = container.querySelector('[data-shortcut-group="pane"]') as HTMLElement;
    const text = paneGroup.textContent ?? '';
    for (const label of [
      '工具箱 · 查找 / 替换面板',
      '工具箱 · 行排序',
      '工具箱 · 转大写',
      '工具箱 · JSON 转义',
      '工具箱 · Base64 编码',
      '工具箱 · JSON 格式化',
      '工具箱 · Markdown 预览',
      '工具箱 · 表格视图',
      '工具箱 · SQL 美化',
      '工具箱 · XML 格式化',
      '工具箱 · 放大字号',
    ]) {
      expect(text, `「文本编辑器级」组里缺少 ${label}`).toContain(label);
    }
    // 行内作用域标签（每条行都有，而不是只在组标题上）
    const chips = (text.match(/编辑面板聚焦/g) ?? []).length;
    expect(chips, '每行都应有作用域标签').toBeGreaterThan(11);
  });

  it('冲突时给出中文横幅，且不静默覆盖（AC-9 既有口径不变）', () => {
    // 直接把**生效键位**改成同组同键：对话框挂载时会真的跑一遍冲突检测（不注入结论）
    const clash = 'Ctrl+Alt+Shift+F11';
    useShortcutStore.setState({
      bindings: { ...DEFAULT_BINDINGS, 'toolbox.lines': clash, 'toolbox.case': clash },
    });
    const container = renderDialog();
    expect(container.textContent ?? '').toContain('存在重复键位');
    expect(container.textContent ?? '').toContain('工具箱 · 行排序');
  });

  it('跨作用域同键给**提示**横幅（含"作用域不同"），而不是把它当成冲突', () => {
    const shared = 'Ctrl+Alt+Shift+F12';
    useShortcutStore.setState({
      bindings: { ...DEFAULT_BINDINGS, 'pane.new': shared, 'toolbox.lines': shared },
    });
    const container = renderDialog();
    const text = container.textContent ?? '';
    expect(text).toContain('跨作用域重复');
    expect(text).toContain('作用域不同');
    expect(text).not.toContain('存在重复键位');
  });

  it('点「修改」进入按键态（既有交互不回归）', () => {
    renderDialog();
    const buttons = screen.getAllByRole('button', { name: '修改' });
    expect(buttons.length).toBeGreaterThan(20);
    fireEvent.click(buttons[0]);
    expect(screen.getByRole('button', { name: '按键中' })).toBeTruthy();
  });
});
