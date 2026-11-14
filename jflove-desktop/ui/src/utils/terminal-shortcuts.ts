/**
 * 终端快捷键配置（v1.10.4）
 *
 * 存在 localStorage：不同人对 Ctrl+C 的期待不同——
 * - `ctrl`：Ctrl+C / Ctrl+V 复制粘贴（Ctrl+C 不再发中断）
 * - `ctrlShift`：Ctrl+Shift+C / Ctrl+Shift+V（保留 Ctrl+C 中断）
 * - `smart`：有选区时 Ctrl+C 复制，无选区时发送中断
 */
export type CopyPasteMode = 'ctrl' | 'ctrlShift' | 'smart';

export const TERMINAL_SHORTCUTS_KEY = 'jflove.terminal.shortcuts';

export interface TerminalShortcuts {
  copyPasteMode: CopyPasteMode;
}

const DEFAULT_SHORTCUTS: TerminalShortcuts = { copyPasteMode: 'ctrl' };

/** 读取快捷键设置（非法值回退默认） */
export function readTerminalShortcuts(): TerminalShortcuts {
  try {
    const raw = localStorage.getItem(TERMINAL_SHORTCUTS_KEY);
    if (!raw) return DEFAULT_SHORTCUTS;
    const parsed = JSON.parse(raw) as Partial<TerminalShortcuts>;
    const mode = parsed.copyPasteMode;
    if (mode === 'ctrl' || mode === 'ctrlShift' || mode === 'smart') {
      return { copyPasteMode: mode };
    }
  } catch {
    // 隐私模式 / 数据损坏时回退默认
  }
  return DEFAULT_SHORTCUTS;
}

/** 写入快捷键设置 */
export function writeTerminalShortcuts(shortcuts: TerminalShortcuts): void {
  try {
    localStorage.setItem(TERMINAL_SHORTCUTS_KEY, JSON.stringify(shortcuts));
  } catch {
    // 忽略：写不进去只影响持久化
  }
}
