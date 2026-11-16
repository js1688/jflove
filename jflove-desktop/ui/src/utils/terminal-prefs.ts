/**
 * 终端偏好（字号 / 自动适配）
 *
 * 与快捷键分开存：字号是"随手调"的显示偏好，快捷键是"一次设定"的行为偏好。
 */
export const TERMINAL_FONT_KEY = 'jflove.terminal.font';

/** 是否自动缩小字号以保证 TUI 行数（默认开） */
export const TERMINAL_AUTOFIT_KEY = 'jflove.terminal.auto-fit-rows';

/** 可选字号范围 */
export const FONT_MIN = 10;
export const FONT_MAX = 22;
export const FONT_DEFAULT = 13;

/**
 * 全屏 TUI 的最小可用行数。
 *
 * 真机事故（v1.10.8）：dsh TUI 在 21 行及以下会把输入框的**内容行**画丢
 * （用 tmux 当参照终端同样复现，属上位程序缺陷）。22 行正常，这里取 24 留余量，
 * 与默认窗口下的观感一致。
 */
export const TUI_MIN_ROWS = 24;

/** 读字号（非法值回退默认） */
export function readTerminalFontSize(): number {
  try {
    const raw = localStorage.getItem(TERMINAL_FONT_KEY);
    const value = Number(raw);
    if (Number.isFinite(value) && value >= FONT_MIN && value <= FONT_MAX) return value;
  } catch {
    // 隐私模式 / 数据损坏时回退默认
  }
  return FONT_DEFAULT;
}

/** 写字号（越界自动夹取） */
export function writeTerminalFontSize(size: number): number {
  const clamped = Math.min(FONT_MAX, Math.max(FONT_MIN, Math.round(size)));
  try {
    localStorage.setItem(TERMINAL_FONT_KEY, String(clamped));
  } catch {
    // 忽略：写不进去只影响持久化
  }
  return clamped;
}

/** 读"窗口偏矮时自动缩小字号"开关（默认开） */
export function readAutoFitRows(): boolean {
  try {
    const raw = localStorage.getItem(TERMINAL_AUTOFIT_KEY);
    if (raw === '0') return false;
  } catch {
    // 隐私模式 / 数据损坏时回退默认
  }
  return true;
}

/** 写"自动缩小字号"开关 */
export function writeAutoFitRows(enabled: boolean): void {
  try {
    localStorage.setItem(TERMINAL_AUTOFIT_KEY, enabled ? '1' : '0');
  } catch {
    // 忽略：写不进去只影响持久化
  }
}
