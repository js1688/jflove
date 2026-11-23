/**
 * 终端粘贴 / 输出调度回归（v1.10.22）
 *
 * ## 事故 1：按一次 Ctrl+V 贴两遍
 *
 * `attachCustomKeyEventHandler` 返回 `false` **只拦 xterm 自己的 `_keyDown`**，
 * 拦不住浏览器默认粘贴。于是同一次按键走两条路：
 *   ① 处理器里的 `readClipboardText()` + `term.paste()`；
 *   ② 浏览器默认粘贴 → xterm 注册的 `paste` 监听 → 再发一次数据。
 * 表现是「按一次贴两遍」。修复判据 = 命中自定义分支时必须 `preventDefault()`。
 *
 * ## 事故 2：输出写屏绑在 rAF 上
 *
 * `flushPending` 用 `requestAnimationFrame` 调度。页面被判隐藏时 rAF
 * **完全停摆**（实测 1 秒 0 帧），窗口藏起来的这段时间终端输出全压在待写队列里，
 * 涨到上限后静默丢数据；恢复时再一次性灌入。判据 = 不再用 rAF 调度写入。
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const source = readFileSync('src/components/remote/TerminalView.tsx', 'utf8');

describe('终端粘贴：单一数据路径（v1.10.22）', () => {
  it('Ctrl+V 自定义分支必须 preventDefault，切断浏览器默认粘贴', () => {
    // 取 attachCustomKeyEventHandler 到其结束的片段
    const start = source.indexOf('term.attachCustomKeyEventHandler');
    expect(start).toBeGreaterThan(0);
    const block = source.slice(start, source.indexOf('window.setTimeout', start));
    expect(block).toContain("if (event.code === 'KeyV')");
    // 关键判据：paste 分支里 preventDefault 必须出现在 pasteClipboard 之前
    const vIndex = block.indexOf("event.code === 'KeyV'");
    const pdIndex = block.indexOf('event.preventDefault()', vIndex);
    const pasteIndex = block.indexOf('void pasteClipboard()', vIndex);
    expect(pdIndex).toBeGreaterThan(vIndex);
    expect(pdIndex).toBeLessThan(pasteIndex);
  });

  it('Ctrl+C 自定义分支同样 preventDefault（避免默认复制与自实现复制叠加）', () => {
    const start = source.indexOf('term.attachCustomKeyEventHandler');
    const block = source.slice(start, source.indexOf('window.setTimeout', start));
    const cIndex = block.indexOf("event.code === 'KeyC'");
    const pdIndex = block.indexOf('event.preventDefault()', cIndex);
    const copyIndex = block.indexOf('void copySelection()', cIndex);
    expect(pdIndex).toBeGreaterThan(cIndex);
    expect(pdIndex).toBeLessThan(copyIndex);
  });

  it('smart 模式下无选区仍放行 Ctrl+C（要能发 SIGINT）', () => {
    const start = source.indexOf('term.attachCustomKeyEventHandler');
    const block = source.slice(start, source.indexOf('window.setTimeout', start));
    expect(block).toContain('return true; // smart 且无选区：交给 shell（SIGINT）');
  });

  it('ctrlShift 模式下裸 Ctrl+V 不拦截（保留终端原生粘贴）', () => {
    const start = source.indexOf('term.attachCustomKeyEventHandler');
    const block = source.slice(start, source.indexOf('window.setTimeout', start));
    expect(block).toContain("if (mode === 'ctrlShift' && !withShift) return true;");
  });
});

describe('终端输出调度：不依赖 rAF（v1.10.22）', () => {
  it('输出写屏不再用 requestAnimationFrame 调度', () => {
    const flushStart = source.indexOf('const flushPending');
    expect(flushStart).toBeGreaterThan(0);
    const flushBlock = source.slice(flushStart, source.indexOf('/** 待写入队列上限', flushStart));
    expect(flushBlock).not.toContain('requestAnimationFrame');
    expect(flushBlock).toContain('scheduleFlush');
  });

  it('入队处也走调度器', () => {
    const enqueueStart = source.indexOf('const enqueueOutput');
    const enqueueBlock = source.slice(enqueueStart, source.indexOf('const clearQueue', enqueueStart));
    expect(enqueueBlock).not.toContain('requestAnimationFrame');
    expect(enqueueBlock).toContain('scheduleFlush');
  });

  it('销毁路径不再 cancelAnimationFrame（已无 rAF 句柄）', () => {
    const disposeStart = source.indexOf('export function disposeTerminalSession');
    const disposeBlock = source.slice(disposeStart, disposeStart + 900);
    expect(disposeBlock).not.toContain('cancelAnimationFrame');
  });

  it('背压丢弃上限仍然存在（不能因改调度把保护弄丢）', () => {
    expect(source).toContain('PENDING_LIMIT');
    expect(source).toContain('丢弃最旧的一段');
  });
});

describe('applyFit 幂等（v1.10.22）', () => {
  it('尺寸/字号/自适应开关未变时直接返回', () => {
    const start = source.indexOf('const applyFit');
    const block = source.slice(start, source.indexOf('const flushPending', start));
    expect(block).toContain('lastFitKey');
    expect(block).toContain('if (fitKey === runtime.lastFitKey) return;');
  });
});
