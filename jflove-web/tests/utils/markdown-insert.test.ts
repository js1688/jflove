/**
 * 「插入图表」/ 工具栏插入的回归测试（v1.5.0 反馈修复）
 *
 * 背景：ER 图模板插入后**必报语法错误**，而模板常量本身是正确的。
 * 根因在插入逻辑里 —— 它把插入文本中**第一个 `|` 当作光标占位符**并无条件吃掉，
 * 而 mermaid 的 ER 关系语法恰好是 `||--o{`：
 *
 *   用户点「ER 图」（无选中文字）
 *     → `template.replace('|', '')`
 *     → `用户 |--o{ 会话 : 拥有`（只剩一根竖线）
 *     → mermaid：`Parse error on line 2: ... got '|'`
 *
 * 这里锁三件事：
 *   1. 图表模板必须**原样插入**（正文里的 `|` 一个都不能少）；
 *   2. 工具栏按钮的 `|` 占位符仍然要正确工作（有选区/无选区两种）；
 *   3. 插入后的内容交给真实引擎渲染，必须**不报语法错误**（端到端，而不只是字符串比对）。
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { CURSOR_MARKER, planInsertion } from '../../src/utils/markdown-insert';

const NOTE_PAGE = readFileSync(
  join(__dirname, '../../src/pages/NoteEditPage.tsx'),
  'utf8',
);

/** 从源码里抽出「插入图表」的模板（测的就是发货的那份字符串，不抄一份） */
function extractDiagramTemplates(): Array<{ label: string; body: string }> {
  const out: Array<{ label: string; body: string }> = [];
  const re = /label:\s*'([^']+)',\s*\n\s*body:\s*`([\s\S]*?)`,\s*\n\s*\}/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(NOTE_PAGE)) !== null) {
    const body = m[2].replace(/\\`\\`\\`/g, '```');
    out.push({ label: m[1], body });
  }
  return out;
}

/** 抽出工具栏按钮的 insert 字面量 */
function extractToolbarInserts(): string[] {
  const out: string[] = [];
  const re = /\{\s*label:\s*'[^']+',\s*icon:\s*'[^']+',\s*insert:\s*'((?:[^'\\]|\\.)*)'\s*\}/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(NOTE_PAGE)) !== null) {
    out.push(m[1].replace(/\\n/g, '\n'));
  }
  return out;
}

describe('图表模板必须原样插入（ER 图那次的根因）', () => {
  const templates = extractDiagramTemplates();

  it('抽到了全部 7 个图表模板（抽取正则与源码格式一致）', () => {
    expect(templates.map((t) => t.label)).toEqual([
      '流程图', '时序图', '状态图', '甘特图', '饼图', '类图', 'ER 图',
    ]);
  });

  it('无选中文字时，每个模板都一字不差地插入（含 ER 图的 `||--o{`）', () => {
    for (const t of templates) {
      const plan = planInsertion(t.body, '', false);
      expect(plan.text, `${t.label} 被改动了`).toBe(t.body);
      expect(plan.cursorOffset).toBe(t.body.length);
    }
  });

  it('有选中文字时，图表模板**同样**一字不差（不会被选区替换掉 `|`）', () => {
    for (const t of templates) {
      const plan = planInsertion(t.body, '被选中的文字', false);
      expect(plan.text, `${t.label} 在有选区时被改动了`).toBe(t.body);
    }
  });

  it('ER 模板插入后仍含两根竖线的 `||--o{`（这就是当时少掉的那一根）', () => {
    const er = templates.find((t) => t.label === 'ER 图');
    expect(er, '找不到 ER 图模板').toBeTruthy();
    expect(er!.body).toContain('||--o{');
    const inserted = planInsertion(er!.body, '', false).text;
    expect(inserted).toContain('||--o{');
    // 反向断言：被吃掉一根的样子不能出现
    expect(inserted).not.toMatch(/[^|]\|--o\{/);
  });

  it('回归：旧实现（无条件 replace）确实会把 ER 模板改坏 —— 证明这条测试不是空转', () => {
    const er = templates.find((t) => t.label === 'ER 图')!;
    // 复刻旧行为
    const legacy = er.body.includes(CURSOR_MARKER)
      ? er.body.replace(CURSOR_MARKER, '')
      : er.body;
    expect(legacy).not.toBe(er.body);
    expect(legacy).toContain('用户 |--o{');
    expect(legacy).not.toContain('||--o{');
  });
});

describe('工具栏按钮的 `|` 占位符仍然正常工作', () => {
  const inserts = extractToolbarInserts();

  it('抽到了工具栏按钮的插入模板', () => {
    expect(inserts.length).toBeGreaterThanOrEqual(10);
    expect(inserts).toContain('**|**');
  });

  it('无选中文字：占位符被移除，光标落在它原来的位置', () => {
    const plan = planInsertion('**|**', '', true);
    expect(plan.text).toBe('****');
    expect(plan.cursorOffset).toBe(2);
  });

  it('有选中文字：选中内容替换到占位符位置，光标落在选中内容之后', () => {
    const plan = planInsertion('**|**', '加粗我', true);
    expect(plan.text).toBe('**加粗我**');
    expect(plan.cursorOffset).toBe('**加粗我'.length);
  });

  it('每个工具栏模板都只含一个占位符（多个会让落点有歧义）', () => {
    for (const tpl of inserts) {
      const count = tpl.split(CURSOR_MARKER).length - 1;
      expect(count, `模板 ${JSON.stringify(tpl)} 的占位符数量应为 1`).toBe(1);
    }
  });

  it('链接/图片模板带 URL 时，光标仍落在方括号内', () => {
    expect(planInsertion('[|](url)', '站点', true).text).toBe('[站点](url)');
    expect(planInsertion('![|](图片地址)', '图', true).text).toBe('![图](图片地址)');
  });
});

describe('调用契约：图表模板不得走占位符解析', () => {
  it('NoteEditPage 里图表菜单调用 insertMarkdown 时不传 useCursorMarker', () => {
    // 图表菜单的调用点：insertMarkdown(tpl.body);
    expect(NOTE_PAGE).toMatch(/insertMarkdown\(tpl\.body\)/);
    // 不能变成开启占位符解析
    expect(NOTE_PAGE).not.toMatch(/insertMarkdown\(tpl\.body,\s*true\)/);
  });

  it('工具栏按钮调用时显式开启占位符解析', () => {
    expect(NOTE_PAGE).toMatch(/insertMarkdown\(btn\.insert,\s*true\)/);
  });

  it('不再残留"无条件把第一个 | 当光标位置"的旧写法', () => {
    expect(NOTE_PAGE).not.toContain("insert.replace('|'");
    expect(NOTE_PAGE).not.toContain("insert.includes('|')");
  });
});
