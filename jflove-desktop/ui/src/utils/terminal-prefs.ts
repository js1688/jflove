/**
 * 终端偏好（字号等）
 *
 * 与快捷键分开存：字号是"随手调"的显示偏好，快捷键是"一次设定"的行为偏好。
 */
export const TERMINAL_FONT_KEY = 'jflove.terminal.font';

/** 可选字号范围 */
export const FONT_MIN = 10;
export const FONT_MAX = 22;
export const FONT_DEFAULT = 13;

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
