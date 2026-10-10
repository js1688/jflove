/**
 * 笔记编辑页回归（v1.5.1 #4-a 大纲侧栏 / #4-b 内容驱动默认模式）
 *
 * 覆盖 AC-6 ~ AC-11：
 *   - `resolveInitialViewMode`：有内容 → `preview`；空 / 纯空白 → `edit`（AC-9 / AC-10）；
 *   - 加载完成后大纲栏是**右侧竖栏**，且 DOM 顺序在预览区**之后**（AC-6）；
 *   - 预览 / 分屏显示大纲、编辑模式不显示（AC-7）；
 *   - 大纲栏可折叠 / 展开，折叠时开关按钮 `data-on` 激活（AC-8）；
 *   - 用户手动切换过模式后不再被自动改写（AC-11）；
 *   - **Phase 5 审查 M-4**：手动切换按 **filename** 记账 —— 同一篇笔记内 AC-11 成立，
 *     切到另一篇笔记时重新按内容判定（有内容 → `preview`、空 → `edit`）。
 *
 * 另外做**源码级结构断言**（`<details>` 底部折叠区必须消失、折叠状态不得落 localStorage）：
 * 这类"结构不能被后续改动悄悄还原"的约束，用文本断言比渲染断言更稳
 * （与 `tests/layouts/navigation-v150.test.ts` 同一套做法；`.claude/skills/LESSONS.md` L12）。
 *
 * 负向对照（L17 / L11）：
 *   - 把 `resolveInitialViewMode` 临时改成恒返回 `'split'` → 本文件必须失败；
 *   - 把「手动切换」的守卫临时改回**全局不复位**（Phase 5 M-4 的现状实现）→ 跨笔记用例必须失败。
 *   两组实际输出见 `文档记录/Web端开发记录/v1.5.1.md` 的自测章节。
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * 被控的笔记内容 +「挂起读取」开关。
 * 用 `vi.hoisted` 是因为 `vi.mock` 的工厂会被提升到 import 之前，不能直接引用普通顶层变量。
 */
const noteStub = vi.hoisted(() => ({
  content: '',
  /** 为真时 `getNote` 返回一个由 `release` 手动兑现的 Promise（复现"加载期间用户就切了模式"） */
  pending: false,
  release: null as null | ((content: string) => void),
}));

vi.mock('../../src/services/note-service', () => ({
  noteService: {
    listNotes: vi.fn(async () => []),
    getNote: vi.fn(() => {
      if (noteStub.pending) {
        return new Promise<string>((res) => {
          noteStub.release = res;
        });
      }
      return Promise.resolve(noteStub.content);
    }),
    saveNote: vi.fn(async () => {}),
    createNote: vi.fn(async () => {}),
    renameNote: vi.fn(async () => {}),
    deleteNote: vi.fn(async () => {}),
    getNotesDiskConfig: vi.fn(async () => ({ disk_id: null, path: '', disks: [] })),
    setNotesDiskConfig: vi.fn(async () => {}),
  },
}));

import { NoteEditPage, resolveInitialViewMode } from '../../src/pages/NoteEditPage';
import { useNoteStore } from '../../src/stores/note-store';

const SRC = resolve(__dirname, '../../src');
const NOTE_EDIT_SRC = readFileSync(resolve(SRC, 'pages/NoteEditPage.tsx'), 'utf8');

/**
 * 去掉注释后的源码 —— 用于"不得出现某用法"的静态断言。
 * 不剥注释的话，说明性注释里出现的关键字（例：`// 不写 localStorage`）会被误判成真实用法。
 */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
}

/** 下一个要切换到的笔记文件名（由用例设置，供 `NavShim` 使用） */
let nextNoteFilename = '第二篇.md';

/**
 * 页内导航垫片：只改 route param（`/notes/:noteId`），**组件实例不卸载**。
 * 用于复现 Phase 5 审查 M-4 的"跨笔记切换"场景 —— 若用"重新挂载"来模拟，
 * ref 会随实例重建，反而测不出"全局布尔不复位"这个缺陷。
 */
function NavShim() {
  const navigate = useNavigate();
  return (
    <>
      <button
        type="button"
        onClick={() => navigate(`/notes/${encodeURIComponent(nextNoteFilename)}`)}
      >
        切换笔记
      </button>
      <NoteEditPage />
    </>
  );
}

/** 以 `/notes/测试笔记.md` 挂载笔记编辑页（路由与真实路由表一致：`notes/:noteId`） */
function renderEditPage() {
  return render(
    <MemoryRouter initialEntries={['/notes/%E6%B5%8B%E8%AF%95%E7%AC%94%E8%AE%B0.md']}>
      <Routes>
        <Route path="/notes/:noteId" element={<NavShim />} />
        <Route path="/notes" element={<div>笔记列表</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  noteStub.content = '';
  noteStub.pending = false;
  noteStub.release = null;
  nextNoteFilename = '第二篇.md';
  // zustand store 是模块级单例，逐个用例复位，避免相互污染
  useNoteStore.setState({
    notes: [],
    notesLoading: false,
    searchQuery: '',
    currentFilename: null,
    currentContent: '',
    originalContent: '',
    isModified: false,
    isLoading: false,
  });
});

describe('resolveInitialViewMode（#4-b 内容驱动默认模式）', () => {
  it('有内容 → 预览模式（AC-9）', () => {
    expect(resolveInitialViewMode('# 标题\n\n正文')).toBe('preview');
    expect(resolveInitialViewMode('x')).toBe('preview');
    expect(resolveInitialViewMode('\n\n正文\n')).toBe('preview');
  });

  it('空串 → 编辑模式（AC-10）', () => {
    expect(resolveInitialViewMode('')).toBe('edit');
  });

  it('纯空白 / 换行 / 制表符 → 编辑模式（AC-10）', () => {
    expect(resolveInitialViewMode('   \n ')).toBe('edit');
    expect(resolveInitialViewMode('\n\n\t  \n')).toBe('edit');
  });

  it('绝不返回 split（分屏不是任何情况的默认值）', () => {
    for (const content of ['', '   ', '# x', 'x']) {
      expect(resolveInitialViewMode(content)).not.toBe('split');
    }
  });
});

describe('笔记编辑页：内容驱动默认模式（#4-b，AC-9 / AC-10 / AC-11）', () => {
  it('有内容的笔记：加载完成后进入预览模式，大纲栏渲染在预览区之后（AC-6 / AC-9）', async () => {
    noteStub.content = '# 一级标题\n\n## 二级标题\n\n正文\n';
    const { container } = renderEditPage();

    await waitFor(() => {
      expect(container.querySelector('.markdown-body')).not.toBeNull();
    });

    const preview = container.querySelector('.markdown-body');
    const previewPane = preview!.parentElement; // 预览区面板（.markdown-body 的外层容器）
    const outline = screen.getByText(/大纲（/).closest('aside');
    expect(outline).not.toBeNull();
    // 与预览区面板**同一个 flex 行容器** ⇒ 是并列的侧栏，而不是原来的底部区域
    // （jsdom 不做布局，只能靠"父容器相同 + DOM 顺序在后"来区分"右侧"与"底部"）
    expect(outline!.parentElement).toBe(previewPane!.parentElement);
    expect(outline!.parentElement!.className).toContain('flex min-h-0 flex-1');
    // DOM 顺序在预览区之后 ⇒ 视觉上就是右侧竖栏，不再是底部折叠区
    expect(
      previewPane!.compareDocumentPosition(outline!) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.getByText('大纲（2 个标题）')).toBeInTheDocument();
    // 预览模式不渲染编辑区
    expect(container.querySelector('textarea')).toBeNull();
  });

  it('空笔记（新建）：停留在编辑模式，不渲染大纲栏（AC-10）', async () => {
    noteStub.content = '';
    const { container } = renderEditPage();

    await waitFor(() => expect(useNoteStore.getState().currentFilename).toBe('测试笔记.md'));
    await waitFor(() => expect(useNoteStore.getState().isLoading).toBe(false));

    expect(container.querySelector('textarea')).not.toBeNull();
    expect(container.querySelector('aside')).toBeNull();
    expect(screen.queryByText(/大纲（/)).toBeNull();
  });

  it('纯空白笔记：同样停留在编辑模式', async () => {
    noteStub.content = '   \n \n';
    const { container } = renderEditPage();

    await waitFor(() => expect(useNoteStore.getState().isLoading).toBe(false));

    expect(container.querySelector('textarea')).not.toBeNull();
    expect(screen.queryByText(/大纲（/)).toBeNull();
  });

  it('加载期间用户手动切到编辑：加载完成后不再被自动改写（AC-11）', async () => {
    noteStub.pending = true;
    const { container } = renderEditPage();

    // 加载还没回来，用户先手动选了「编辑」
    fireEvent.click(screen.getByRole('tab', { name: '编辑' }));
    await waitFor(() => expect(noteStub.release).not.toBeNull());
    noteStub.release!('# 已有标题\n\n正文\n');

    await waitFor(() => expect(useNoteStore.getState().isLoading).toBe(false));
    // 内容非空本来是 "preview"，但用户手动切换优先 ⇒ 必须停在 edit
    expect(container.querySelector('textarea')).not.toBeNull();
    expect(container.querySelector('.markdown-body')).toBeNull();
  });
});

describe('跨笔记切换：手动切换按 filename 记账（Phase 5 审查 M-4）', () => {
  it('① 同一篇笔记内手动切换后，内容变化不再改变模式（AC-11）', async () => {
    noteStub.content = '# 一级标题\n\n正文\n';
    const { container } = renderEditPage();

    await screen.findByText('大纲（1 个标题）'); // 自动判定 → preview
    fireEvent.click(screen.getByRole('tab', { name: '编辑' }));
    expect(container.querySelector('textarea')).not.toBeNull();

    // 内容变化（自动保存会反复触发 store 更新）不得把模式改回去
    await act(async () => {
      useNoteStore.getState().setContent('# 一级标题\n\n正文\n追加一段\n');
    });
    expect(container.querySelector('textarea')).not.toBeNull();
    expect(container.querySelector('.markdown-body')).toBeNull();

    // 反向：手动切回 preview 后继续改内容，仍是 preview
    fireEvent.click(screen.getByRole('tab', { name: '预览' }));
    expect(container.querySelector('.markdown-body')).not.toBeNull();
    await act(async () => {
      useNoteStore.getState().setContent('# 一级标题\n\n正文\n再追加一段\n');
    });
    expect(container.querySelector('.markdown-body')).not.toBeNull();
    expect(screen.getByRole('tab', { name: '预览' })).toHaveAttribute('aria-selected', 'true');
  });

  it('② 切到另一篇**有内容**的笔记：重新按内容判定为预览（M-4 回归 / AC-9）', async () => {
    noteStub.content = '# 甲\n\n甲正文\n';
    const { container } = renderEditPage();

    await screen.findByText('大纲（1 个标题）');
    // 在**第一篇**里手动切到编辑：旧实现（全局布尔不复位）会让此后所有笔记都停在 edit
    fireEvent.click(screen.getByRole('tab', { name: '编辑' }));
    expect(container.querySelector('textarea')).not.toBeNull();

    // 切到另一篇有内容的笔记 —— 只改 route param，组件实例不卸载
    noteStub.content = '# 乙\n\n乙正文\n';
    nextNoteFilename = '第二篇.md';
    fireEvent.click(screen.getByRole('button', { name: '切换笔记' }));

    await screen.findByText('大纲（1 个标题）');
    expect(screen.getByRole('tab', { name: '预览' })).toHaveAttribute('aria-selected', 'true');
    expect(container.querySelector('.markdown-body')?.textContent).toContain('乙');
  });

  it('② 切到另一篇**空**笔记：重新按内容判定为编辑（M-4 回归 / AC-10）', async () => {
    noteStub.content = '# 甲\n\n甲正文\n';
    const { container } = renderEditPage();

    await screen.findByText('大纲（1 个标题）');
    // 手动切到**分屏**：若跨笔记不复位，切过去会错误地停在 split
    fireEvent.click(screen.getByRole('tab', { name: '分屏' }));
    expect(container.querySelector('.markdown-body')).not.toBeNull();

    noteStub.content = '';
    nextNoteFilename = '第二篇.md';
    fireEvent.click(screen.getByRole('button', { name: '切换笔记' }));

    await waitFor(() => expect(useNoteStore.getState().isLoading).toBe(false));
    expect(screen.getByRole('tab', { name: '编辑' })).toHaveAttribute('aria-selected', 'true');
    expect(container.querySelector('textarea')).not.toBeNull();
    expect(container.querySelector('.markdown-body')).toBeNull();
  });
});

describe('笔记编辑页：大纲栏显示与折叠（#4-a，AC-7 / AC-8）', () => {
  it('大纲栏可折叠 / 展开，折叠时开关按钮 data-on 激活（AC-8）', async () => {
    noteStub.content = '# 一级标题\n\n## 二级标题\n';
    renderEditPage();

    await screen.findByText('大纲（2 个标题）');
    const toggle = screen.getByRole('button', { name: '折叠大纲' });
    expect(toggle).toHaveAttribute('data-on', 'false');

    fireEvent.click(toggle);
    expect(screen.queryByText(/大纲（/)).toBeNull();
    const expand = screen.getByRole('button', { name: '展开大纲' });
    expect(expand).toHaveAttribute('data-on', 'true');

    fireEvent.click(expand);
    expect(screen.getByText('大纲（2 个标题）')).toBeInTheDocument();
  });

  it('分屏模式同样显示大纲栏（AC-7）', async () => {
    noteStub.content = '# 一级标题\n';
    const { container } = renderEditPage();

    await screen.findByText('大纲（1 个标题）');
    fireEvent.click(screen.getByRole('tab', { name: '分屏' }));

    expect(container.querySelector('textarea')).not.toBeNull();
    expect(container.querySelector('.markdown-body')).not.toBeNull();
    expect(screen.getByText('大纲（1 个标题）')).toBeInTheDocument();
  });

  it('编辑模式不渲染大纲栏与大纲开关（跳转依赖预览区 DOM）', async () => {
    noteStub.content = '# 一级标题\n';
    renderEditPage();

    await screen.findByText('大纲（1 个标题）');
    fireEvent.click(screen.getByRole('tab', { name: '编辑' }));

    expect(screen.queryByText(/大纲（/)).toBeNull();
    expect(screen.queryByRole('button', { name: /大纲/ })).toBeNull();
  });

  it('无标题的笔记不渲染大纲栏', async () => {
    noteStub.content = '只有正文，没有任何标题\n';
    const { container } = renderEditPage();

    await waitFor(() => expect(container.querySelector('.markdown-body')).not.toBeNull());

    expect(container.querySelector('aside')).toBeNull();
    expect(screen.queryByText(/大纲（/)).toBeNull();
    expect(screen.queryByRole('button', { name: /大纲/ })).toBeNull();
  });
});

describe('NoteEditPage 源码级结构断言（#4-a / #4-b）', () => {
  it('底部折叠区大纲已移除（不再有 <details> / <summary>）', () => {
    expect(NOTE_EDIT_SRC).not.toContain('<details');
    expect(NOTE_EDIT_SRC).not.toContain('</details>');
    expect(NOTE_EDIT_SRC).not.toContain('<summary');
  });

  it('大纲是右侧竖栏：w-56 + border-l + overflow-y-auto + bg-surface', () => {
    expect(NOTE_EDIT_SRC).toContain(
      'w-56 shrink-0 overflow-y-auto border-l border-line-subtle bg-surface',
    );
  });

  it('显示条件 = 预览/分屏 + 有标题', () => {
    expect(NOTE_EDIT_SRC).toContain(
      "(viewMode === 'preview' || viewMode === 'split') && headings.length > 0",
    );
  });

  it('默认展开，且折叠状态只放组件内存（不写 localStorage）', () => {
    expect(NOTE_EDIT_SRC).toContain('const [outlineOpen, setOutlineOpen] = useState(true)');
    expect(stripComments(NOTE_EDIT_SRC)).not.toMatch(/localStorage|sessionStorage/);
  });

  it('默认模式判定被抽成 pure function 并 export（供单测导入）', () => {
    expect(NOTE_EDIT_SRC).toContain('export function resolveInitialViewMode(content: string): ViewMode');
  });

  it('判定只发生在加载成功之后，且受"该 filename 已判定过 / 该 filename 手动切换过"两个 ref 约束', () => {
    // M-4：**按 filename** 记账，不是一个全局布尔值（否则跨笔记不复位，违背 AC-9）
    expect(NOTE_EDIT_SRC).toContain('if (userSwitchedModeForRef.current === filename) return;');
    expect(NOTE_EDIT_SRC).toContain('if (modeResolvedForRef.current === filename) return;');
    expect(NOTE_EDIT_SRC).toContain('const userSwitchedModeForRef = useRef<string | null>(null);');
    // 手动切换必须按 filename 打标记
    expect(NOTE_EDIT_SRC).toContain('userSwitchedModeForRef.current = filename;');
    // 防回退：不得再出现"全局布尔版"的手动切换标记
    expect(NOTE_EDIT_SRC).not.toContain('userSwitchedModeRef');
  });
});
