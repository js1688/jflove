/**
 * **AC-74 短语表**守门用例（本批 C1 的 CM6 面板部分）—— 需求 AC-74①~⑤ / 设计 v1.6.1 §15.2.1
 *
 * ## 判据出处
 *
 * - **AC-74①②③④⑤**（需求 `文档记录/需求文档/v1.6.1.md` §9.2）：查找 / 替换 / 跳转行面板、
 *   折叠占位符、控制字符提示、diff 折叠提示（「未更改的 $ 行」）**全部为中文**，
 *   命中对应英文原文即失败；
 * - **设计 §15.0.1 M1~M4（已实测）**：`search({phrases})` 是 **no-op**，唯一正确做法是
 *   `EditorState.phrases.of({...})`；`phrase()` 找不到键时**返回键本身**（⇒ 漏翻译表现为英文）；
 *   `$` 是插值占位符（`phrase('$ unchanged lines', 7)` ⇒ `"未更改的 7 行"`）；
 * - **设计 §15.2.1**：短语表放**零依赖叶子** `utils/scratch/editor-phrases.ts`；
 *   `installEditorPhrases(api)` 必须**防御式**（`api.EditorState.phrases` 缺失 ⇒ 返回 `[]`，绝不抛）。
 *
 * ## 键集是怎么来的（**可复现**，不是猜的）
 *
 * 自动化部分（本文件内直接扫离线包）：`phrase("…")` 的字面量（实测 **17** 条）。
 * 手工部分（本文件用**显式清单**表达，取证命令写在断言旁）：查找 / 替换面板的 **11** 条
 * （它们经 `dt(e,"…")` 调用，即 `function dt(t,e){return t.state.phrase(e)}`）+ 折叠槽的 **4** 条
 * （三元表达式实参：`"Fold line"` / `"Unfold line"` / `"Folded lines"` / `"Unfolded lines"`，
 * 字面量正则抓不到，必须读上下文）。⇒ 去重后 **31** 条。
 *
 * ⚠ 与设计文档的口径偏差（**如实登记**）：设计 §15.2.1 的正文写"17 条（实测全集）"，
 * 但示例对象里实际列了 **27** 键；本实现 = 那 27 键 **+** 折叠槽 4 条 = **31** 键
 * （多出的 4 条同样是用户可见文案：折叠箭头 `title` 与 `aria-live` 朗读文本）。
 */

import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { CM_BUNDLE_PATH, loadCmBundle } from '../../../test/cm-bundle';
import type { JFLoveCmApi } from '../../../types/codemirror-bundle';
import { EDITOR_PHRASES, installEditorPhrases } from '../editor-phrases';

/** 中文（CJK 统一表意文字）判据：值里必须至少有一个汉字 ⇒ "0 命中英文原文"的可判定形态 */
const CJK = /[\u4e00-\u9fff]/;

/**
 * 查找 / 替换面板的键（离线包里经 `dt(e,"…")` 调用）。
 *
 * 取证命令（`jflove-desktop/ui` 下）：
 * `[regex]::Matches((Get-Content -Raw public/vendor/editor.bundle.js), 'dt\(e,"([^"]*)"\)')`
 */
const SEARCH_PANEL_KEYS = [
  'Find',
  'Replace',
  'next',
  'previous',
  'all',
  'match case',
  'regexp',
  'by word',
  'replace',
  'replace all',
  'close',
] as const;

/**
 * 折叠槽的键（离线包里是三元表达式实参，字面量正则抓不到）。
 *
 * 取证命令：在离线包内检索 `Fold line` / `Folded lines` ——
 * `code.Substring(index-300, 460)`，实测片段：
 * `O.title=e.state.phrase(this.open?"Fold line":"Unfold line")`、
 * ``N.announce.of(`${t.state.phrase(O?"Folded lines":"Unfolded lines")} …`)``
 */
const FOLD_GUTTER_KEYS = ['Fold line', 'Unfold line', 'Folded lines', 'Unfolded lines'] as const;

describe('EDITOR_PHRASES：覆盖实测全集且逐条真译（不是"键存在"式断言）', () => {
  it('离线包里 `phrase("…")` 的**字面量全集**（实测 17 条）必须全部被覆盖，且不得退化为英文', () => {
    const code = readFileSync(CM_BUNDLE_PATH, 'utf8');
    const literals = Array.from(code.matchAll(/phrase\("((?:[^"\\]|\\.)*)"/g)).map((m) => m[1]);
    const unique = Array.from(new Set(literals)).sort();

    // 前置自证：扫描确实抓到了字面量（离线包被换掉 / 正则失效时必须先在这里红）
    expect(unique.length).toBeGreaterThanOrEqual(17);
    // 设计 §15.0.1 M4 记录的 17 条（逐字）—— 少一条就说明扫描或覆盖率出问题
    expect(unique).toEqual(
      [
        '$ unchanged lines',
        'Accept',
        'Completions',
        'Control character',
        'Go to line',
        'Reject',
        'Revert this chunk',
        'Selection deleted',
        'close',
        'current match',
        'folded code',
        'go',
        'on line',
        'replaced $ matches',
        'replaced match on line $',
        'to',
        'unfold',
      ].sort(),
    );

    for (const key of unique) {
      // ★ 逐键：必须真的有中文译名（缺失 ⇒ `phrase()` 回落英文原文，AC-74 直接失败）
      expect(EDITOR_PHRASES[key], `短语表缺少 CM6 字面量键：${key}`).toBeTruthy();
      expect(EDITOR_PHRASES[key], `短语表 ${key} 仍是英文原文`).not.toBe(key);
      expect(EDITOR_PHRASES[key], `短语表 ${key} 不是中文`).toMatch(CJK);
    }
  });

  it('覆盖查找 / 替换面板的 11 条与折叠槽的 4 条（含 AC-74①②③ 点名的面板）', () => {
    for (const key of [...SEARCH_PANEL_KEYS, ...FOLD_GUTTER_KEYS]) {
      expect(EDITOR_PHRASES[key], `短语表缺少面板键：${key}`).toBeTruthy();
      expect(EDITOR_PHRASES[key]).not.toBe(key);
      expect(EDITOR_PHRASES[key]).toMatch(CJK);
    }
    // AC-74①② 点名的两条面板主文案（判据里的字面要求）
    expect(EDITOR_PHRASES.Find).toBe('查找');
    expect(EDITOR_PHRASES.Replace).toBe('替换');
    expect(EDITOR_PHRASES['Go to line']).toBe('跳转到行');
  });

  it('`$` 插值占位符必须原样保留（不得写成 $1 / ${n}，也不得删掉）', () => {
    // 设计 §15.0.1 M3：`phrase('$ unchanged lines', 7)` ⇒ `"未更改的 7 行"`
    expect(EDITOR_PHRASES['$ unchanged lines']).toContain('$');
    expect(EDITOR_PHRASES['replaced $ matches']).toContain('$');
    expect(EDITOR_PHRASES['replaced match on line $']).toContain('$');
    expect(Object.values(EDITOR_PHRASES).join()).not.toContain('${');
    // 除这 3 条外，其余译名不得混入 `$`（否则会被 CM6 当占位符吃掉后半个词）
    const dollarKeys = Object.entries(EDITOR_PHRASES)
      .filter(([, value]) => value.includes('$'))
      .map(([key]) => key)
      .sort();
    expect(dollarKeys).toEqual(
      ['$ unchanged lines', 'replaced $ matches', 'replaced match on line $'].sort(),
    );
  });
});

describe('installEditorPhrases：防御式（缺失 phrases 时绝不崩）', () => {
  it('`api.EditorState.phrases` 缺失 / 形状不对 ⇒ 返回 `[]` 且不抛（老包 / 新包混用）', () => {
    const empty = {} as JFLoveCmApi;
    expect(() => installEditorPhrases(empty)).not.toThrow();
    expect(installEditorPhrases(empty)).toEqual([]);

    const noEditorState = { EditorState: {} } as unknown as JFLoveCmApi;
    expect(installEditorPhrases(noEditorState)).toEqual([]);

    const badOf = {
      EditorState: { phrases: { of: 'not-a-function' } },
    } as unknown as JFLoveCmApi;
    expect(installEditorPhrases(badOf)).toEqual([]);
  });

  it('真离线包：返回恰好 1 个扩展，且 facet 里就是那份表（浅拷贝，不共享引用）', () => {
    const api = loadCmBundle();
    const extensions = installEditorPhrases(api);
    expect(extensions).toHaveLength(1);

    // 经**真** facet 读取：值必须是"含 31 条译名"的对象，且**不是** EDITOR_PHRASES 本体
    // （浅拷贝的理由见设计 §15.2.1：防某个消费方就地改字典污染全局表）
    const state = api.EditorState.create({ extensions });
    // 手写类型把 `phrases` 声明为 `StateFacet<Record<string, string>>`，而**真实** facet 值
    // 是"各扩展输入值的数组"（`Facet.define()` 无 combine 时即如此，M2 实测）
    // ⇒ 这里按真实形状收窄（这正是 M2 记录的"数组里靠前的扩展胜出"那一层）
    const facetValue = state.facet(api.EditorState.phrases) as unknown as readonly Record<
      string,
      string
    >[];
    expect(facetValue).toHaveLength(1);
    expect(Object.keys(facetValue[0]).length).toBe(Object.keys(EDITOR_PHRASES).length);
    expect(facetValue[0]).not.toBe(EDITOR_PHRASES);
    expect(facetValue[0].Find).toBe('查找');
  });
});

describe('真离线包：`state.phrase()` 逐键返回中文（AC-74 的可判定形态）', () => {
  it('装了短语表后，31 条键经**真** `phrase()` 全部返回中文；未装时回落英文键本身（判别力自证）', () => {
    const api = loadCmBundle();
    const localized = api.EditorState.create({ extensions: installEditorPhrases(api) });
    const bare = api.EditorState.create({ extensions: [] });

    const keys = Object.keys(EDITOR_PHRASES);
    expect(keys.length).toBe(31);
    for (const key of keys) {
      expect(localized.phrase(key), `phrase(${key}) 未本地化`).toBe(EDITOR_PHRASES[key]);
      expect(localized.phrase(key)).toMatch(CJK);
      // 负向自证：没有短语表时 CM6 回落的正是**英文键本身** ⇒ 上面那条断言确实在测本地化
      expect(bare.phrase(key)).toBe(key);
    }
  });

  it('`$` 占位符在真 `phrase()` 里被实参替换（AC-74⑤ 的口径：`未更改的 7 行`）', () => {
    const api = loadCmBundle();
    const state = api.EditorState.create({ extensions: installEditorPhrases(api) });

    expect(state.phrase('$ unchanged lines', 7)).toBe('未更改的 7 行');
    expect(state.phrase('replaced $ matches', 3)).toBe('已替换 3 处匹配');
    expect(state.phrase('replaced match on line $', 12)).toBe('已替换第 12 行的匹配');
    // AC-74⑤ 的负向面：不得出现 `unchanged`
    expect(state.phrase('$ unchanged lines', 7)).not.toContain('unchanged');
  });
});
