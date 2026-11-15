/**
 * DECRQM 兼容层单测（v1.10.8）
 *
 * 背景：xterm 6.0.0 的生产包对 DECRQM（`CSI ? m $ p`）会抛
 * `ReferenceError`，而 TUI 每帧都探测终端能力 → 终端永久卡死。
 *
 * 这里钉住两件事：
 * 1. 状态码映射符合 DECRPM 语义（1 置位 / 2 复位 / 3 永久置位 / 4 永久复位 / 0 未识别）；
 * 2. 回复串格式正确（私有模式带 `?`，ANSI 模式不带）。
 */
import { describe, expect, it } from 'vitest';
import {
  DECRPM,
  buildDecrpmReply,
  decrpmStatus,
  type TerminalModeSnapshot,
} from '../utils/xterm-decrqm';

const base: TerminalModeSnapshot = {
  applicationCursorKeysMode: false,
  applicationKeypadMode: false,
  bracketedPasteMode: true,
  insertMode: false,
  mouseTrackingMode: 'any',
  originMode: false,
  reverseWraparoundMode: true,
  sendFocusMode: true,
  synchronizedOutputMode: false,
  wraparoundMode: true,
  altScreenActive: true,
};

describe('decrpmStatus', () => {
  it('私有模式按终端当前状态回答', () => {
    expect(decrpmStatus(1, false, base)).toBe(DECRPM.RESET);
    expect(decrpmStatus(2004, false, base)).toBe(DECRPM.SET);
    expect(decrpmStatus(2026, false, base)).toBe(DECRPM.RESET);
    expect(decrpmStatus(1049, false, base)).toBe(DECRPM.SET);
    expect(decrpmStatus(1004, false, base)).toBe(DECRPM.SET);
  });

  it('鼠标协议按当前模式回答（1003 = any）', () => {
    expect(decrpmStatus(1003, false, base)).toBe(DECRPM.SET);
    expect(decrpmStatus(1000, false, base)).toBe(DECRPM.RESET);
    expect(decrpmStatus(1002, false, base)).toBe(DECRPM.RESET);
    expect(decrpmStatus(1006, false, base)).toBe(DECRPM.SET);
  });

  it('未知模式回 0（未识别），不瞎猜', () => {
    expect(decrpmStatus(9999, false, base)).toBe(DECRPM.NOT_RECOGNIZED);
    expect(decrpmStatus(9999, true, base)).toBe(DECRPM.NOT_RECOGNIZED);
  });

  it('ANSI 模式按规范回答固定值', () => {
    expect(decrpmStatus(2, true, base)).toBe(DECRPM.PERMANENTLY_RESET);
    expect(decrpmStatus(12, true, base)).toBe(DECRPM.PERMANENTLY_SET);
    expect(decrpmStatus(4, true, base)).toBe(DECRPM.RESET);
  });
});

describe('buildDecrpmReply', () => {
  it('私有模式带 ?，ANSI 模式不带', () => {
    expect(buildDecrpmReply(1049, false, DECRPM.SET)).toBe('\u001b[?1049;1$y');
    expect(buildDecrpmReply(4, true, DECRPM.RESET)).toBe('\u001b[4;2$y');
  });
});
