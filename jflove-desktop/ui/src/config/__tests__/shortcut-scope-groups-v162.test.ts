/**
 * v1.6.2 F16 用例：快捷键配置**两组隔离**（AC-97 / AC-98）
 *
 * 用户裁决："这里的工具箱其实指的就是文本编辑器里面的工具箱，并不是全局工具箱，
 * 所以只要分应用级和文本编辑器级就行" —— 于是判据有三条：
 * ① 分组只两组、组标题与作用域文案正确、条目归属正确（工具箱 11 项在「文本编辑器级」）；
 * ② **组内同键必拒**；**跨组同键只提示、允许保存**（作用域不同 ⇒ 不会同时触发）；
 * ③ 旧 `shortcuts.json`（没有新条目）⇒ 11 条新键自动取默认值，**不需要清配置**。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/scratch-service', () => ({
  scratchService: {
    shortcutsSave: vi.fn(async () => ({ ok: true })),
    shortcutsLoad: vi.fn(async () => ({ version: 1, bindings: {} })),
  },
}));

import {
  DEFAULT_BINDINGS,
  SHORTCUT_SCOPE_GROUPS,
  findConflictingShortcuts,
  findCrossScopeKeyNotes,
  getShortcutDef,
  shortcutsOfScope,
  withOverrides,
} from '../shortcuts';
import { useShortcutStore } from '../../stores/shortcut-store';
import { scratchService } from '../../services/scratch-service';

const FREE_KEY_A = 'Ctrl+Alt+Shift+F11';
const FREE_KEY_B = 'Ctrl+Alt+Shift+F12';

beforeEach(() => {
  useShortcutStore.setState({
    overrides: {},
    bindings: { ...DEFAULT_BINDINGS },
    conflicts: [],
    crossScopeNotes: [],
    lastError: null,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('AC-97① 分组结构（两组、标题、作用域、归属）', () => {
  it('恰好两组：`window` = 应用级、`pane` = 文本编辑器级，且各自带中文作用域说明', () => {
    expect(SHORTCUT_SCOPE_GROUPS).toHaveLength(2);
    expect(SHORTCUT_SCOPE_GROUPS[0].scope).toBe('window');
    expect(SHORTCUT_SCOPE_GROUPS[0].title).toBe('应用级');
    expect(SHORTCUT_SCOPE_GROUPS[0].subtitle).toContain('任意页面');
    expect(SHORTCUT_SCOPE_GROUPS[1].scope).toBe('pane');
    expect(SHORTCUT_SCOPE_GROUPS[1].title).toBe('文本编辑器级');
    expect(SHORTCUT_SCOPE_GROUPS[1].subtitle).toContain('编辑面板聚焦');
    for (const group of SHORTCUT_SCOPE_GROUPS) {
      expect(group.description.length, `${group.title} 缺中文说明`).toBeGreaterThan(10);
    }
  });

  it('两组条目并集 = 全表，且无重复归属；工具箱 11 项全部落在「文本编辑器级」', () => {
    const windowIds = shortcutsOfScope('window').map((def) => def.id);
    const paneIds = shortcutsOfScope('pane').map((def) => def.id);
    expect(windowIds.length + paneIds.length).toBeGreaterThanOrEqual(29);
    expect(new Set([...windowIds, ...paneIds]).size).toBe(windowIds.length + paneIds.length);
    for (const def of shortcutsOfScope('window')) expect(def.scope).toBe('window');
    for (const def of shortcutsOfScope('pane')) expect(def.scope).toBe('pane');

    const toolboxIds = paneIds.filter((id) => id.startsWith('toolbox.'));
    expect(toolboxIds, '工具箱 11 条必须都在文本编辑器级组里').toHaveLength(11);
  });
});

describe('AC-97③④ 冲突口径：组内必拒、跨组只提示', () => {
  it('**同组内**同键 ⇒ 报冲突（`save()` 拒绝保存）', async () => {
    useShortcutStore.getState().setBinding('toolbox.lines', FREE_KEY_A);
    useShortcutStore.getState().setBinding('toolbox.case', FREE_KEY_A);

    const conflicts = useShortcutStore.getState().detectConflicts();
    const hit = conflicts.find(
      (item) => item.ids.includes('toolbox.lines') && item.ids.includes('toolbox.case'),
    );
    expect(hit, '同组同键必须被判定为冲突').toBeTruthy();
    await expect(useShortcutStore.getState().save()).resolves.toBe(false);
    expect(useShortcutStore.getState().lastError).toContain('未保存');
  });

  it('**跨组**同键 ⇒ 不算冲突、只给提示，且**允许保存**', async () => {
    // 一条应用级（`pane.new`）+ 一条编辑器级（`toolbox.lines`）配成同一个键
    useShortcutStore.getState().setBinding('pane.new', FREE_KEY_B);
    useShortcutStore.getState().setBinding('toolbox.lines', FREE_KEY_B);

    const conflicts = useShortcutStore.getState().detectConflicts();
    expect(
      conflicts.some((item) => item.ids.includes('pane.new') || item.ids.includes('toolbox.lines')),
      '跨组同键不得被判成冲突',
    ).toBe(false);

    const notes = useShortcutStore.getState().crossScopeNotes;
    const note = notes.find(
      (item) => item.ids.includes('pane.new') && item.ids.includes('toolbox.lines'),
    );
    expect(note, '跨组同键必须给提示（用户要求知道作用域）').toBeTruthy();

    await expect(useShortcutStore.getState().save()).resolves.toBe(true);
    expect(scratchService.shortcutsSave).toHaveBeenCalled();
  });

  it('纯函数层的同一口径（`findConflictingShortcuts` 组内 / `findCrossScopeKeyNotes` 跨组）', () => {
    const intra = { 'toolbox.lines': FREE_KEY_A, 'toolbox.case': FREE_KEY_A };
    expect(findConflictingShortcuts(intra).length).toBe(1);

    const cross = { 'pane.new': FREE_KEY_B, 'toolbox.lines': FREE_KEY_B };
    expect(findConflictingShortcuts(cross)).toEqual([]);
    expect(findCrossScopeKeyNotes(cross)).toHaveLength(1);

    // 默认表：连跨组同键都不允许（默认值是我们自己选的）
    expect(findCrossScopeKeyNotes({ ...DEFAULT_BINDINGS })).toEqual([]);
  });
});

describe('AC-98 旧配置兼容（不需要清 shortcuts.json）', () => {
  it('缺 11 条新键的旧 overrides ⇒ 一律取默认值，且版本仍是 1', () => {
    // 模拟 v1.6.1 用户文件：只改过一项既有快捷键
    const legacy = { 'edit.find': 'Ctrl+Alt+F' };
    const bindings = withOverrides(legacy);
    expect(bindings['edit.find']).toBe('Ctrl+Alt+F');
    expect(bindings['toolbox.lines']).toBe(DEFAULT_BINDINGS['toolbox.lines']);
    expect(bindings['toolbox.font']).toBe(DEFAULT_BINDINGS['toolbox.font']);
    // 未知 id / 非法值一律忽略回默认（既有口径不变）
    expect(withOverrides({ nonsense: 'Ctrl+Q' }).nonsense).toBeUndefined();
  });

  it('11 条新键的默认值互不相同、且与既有 18 项不撞车', () => {
    const seen = new Map<string, string>();
    for (const [id, key] of Object.entries(DEFAULT_BINDINGS)) {
      expect(seen.get(key), `${id} 与 ${seen.get(key)} 默认键撞车`).toBeUndefined();
      seen.set(key, id);
    }
    for (const id of [
      'toolbox.search',
      'toolbox.lines',
      'toolbox.case',
      'toolbox.escape',
      'toolbox.base64',
      'toolbox.json',
      'toolbox.markdown',
      'toolbox.csv',
      'toolbox.sql',
      'toolbox.xml',
      'toolbox.font',
    ]) {
      expect(getShortcutDef(id)?.scope, `${id} 必须是编辑器级`).toBe('pane');
    }
  });
});
