/**
 * AC-79：文件关联界面写清**三平台范围** + 诚实登记未验证项
 *
 * ## 判据出处
 *
 * - 需求 `文档记录/需求文档/v1.6.1.md` **AC-79**（① Windows/Linux/macOS 各 ≥1 次
 *   ②「macOS 暂不支持」③ Linux 与 Windows 的差异对照 ④「Linux 侧尚未真机验证」
 *   ⑤ 行为与平台一致 ⑥ 不改既有判据 —— 勾选清单仍由类型表派生、仍不抢占默认程序）；
 * - 设计 `文档记录/设计文档/v1.6.1.md` **§15.7.2**（要写入界面的确切中文文本 + 自检表）。
 *
 * ## 为什么"失败得起来"
 *
 * 改造前那段语义说明（`FileAssocSettings.tsx` 三段 `<div>`）**完全没提平台**：
 * "三平台各 ≥1 次命中"「暂不支持」这两条判据当时**都不成立**。用例按判据逐条断言，
 * 删掉任何一句平台说明都会有对应用例变红。
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { createElement } from 'react';

vi.mock('../../../services/scratch-service', () => ({
  scratchService: {
    fileAssocGet: vi.fn(async () => ({
      platform: 'win32',
      selected: [],
      supported: ['json', 'csv'],
      osRegistered: true,
      note: '',
    })),
    fileAssocSet: vi.fn(async () => ({ selected: [] })),
    fileAssocOpenSystemDefaults: vi.fn(async () => ({ ok: true, hint: '' })),
  },
}));

import { FileAssocSettings } from '../FileAssocSettings';
import { EXTENSIONS } from '../../../config/scratch-types';

/** 渲染并等首次读取完成（语义说明是静态文案，与读取结果无关，但要避开 loading 态） */
async function renderSettings(): Promise<HTMLElement> {
  const view = render(createElement(FileAssocSettings));
  await waitFor(() => {
    expect(screen.queryByText(/正在读取文件关联状态/)).toBeNull();
  });
  return view.container;
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('AC-79①② 三平台范围 + macOS 暂不支持', () => {
  it('文案里「Windows」「Linux」「macOS」三词各出现 ≥1 次', async () => {
    const container = await renderSettings();
    const text = container.textContent ?? '';
    for (const token of ['Windows', 'Linux', 'macOS']) {
      expect(text, `文案缺平台说明：${token}`).toContain(token);
    }
  });

  it('macOS 的说明里含「暂不支持」（同一句）', async () => {
    const container = await renderSettings();
    const text = container.textContent ?? '';
    // 判据："出现 macOS 的说明中含'不支持'/'暂不支持'"
    const around = text.slice(Math.max(0, text.indexOf('macOS')), text.indexOf('macOS') + 24);
    expect(around, `macOS 附近没有「暂不支持」：${around}`).toMatch(/暂不支持|不支持/);
  });

  it('明确写出 Linux 与 Windows 的差异：Windows 直接打开设置页 vs Linux 请自行在桌面环境选择', async () => {
    const container = await renderSettings();
    const text = container.textContent ?? '';
    expect(text).toContain('会直接打开系统的「默认应用」设置页');
    expect(text).toContain('在桌面环境的「默认应用」里');
    expect(text).toContain('自行选择');
    // Linux 侧不得写成"也直接打开（Windows 的）设置页"
    expect(text).toContain('不打开 Windows 设置页');
  });
});

describe('AC-79④ 诚实登记「Linux 侧未真机验证」', () => {
  it('同屏出现「尚未真机验证」，且**不得**与「已验证」并存', async () => {
    const container = await renderSettings();
    const text = container.textContent ?? '';
    expect(text).toContain('Linux 侧尚未真机验证');
    expect(text, '不得出现「已验证」').not.toContain('已验证');
    // 禁止出现的三种越界措辞（设计 §15.7.3 的"诚实登记措辞"）
    expect(text).not.toContain('全平台支持');
    expect(text).not.toContain('macOS 支持');
    expect(text).not.toContain('Linux 已验证');
  });
});

describe('AC-79⑥ 不改既有判据（勾选清单仍由类型表派生）', () => {
  it('勾选清单仍逐项来自 `EXTENSIONS`（扩展名与显示名同源）', async () => {
    const container = await renderSettings();
    const boxes = container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]');
    expect(boxes.length, '勾选清单条目数应等于类型表条目数').toBe(EXTENSIONS.length);
    // 抽查两个本轮改过 label 的项：显示名必须与类型表一致（派生链的单一真相）
    const labelOf = (ext: string): string => EXTENSIONS.find((item) => item.ext === ext)?.label ?? '';
    expect(container.textContent).toContain(labelOf('jsonc'));
    expect(container.textContent).toContain(labelOf('diff'));
  });

  it('语义说明仍然写明「不会修改、也不会抢占系统的默认程序」与「无论是否勾选都会用 JFLove 打开」', async () => {
    const container = await renderSettings();
    const text = container.textContent ?? '';
    expect(text).toContain('不会修改、也不会抢占系统的默认程序');
    expect(text).toContain('无论是否勾选都会用 JFLove 打开');
  });
});
