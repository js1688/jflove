/**
 * AC-74 显示名判据（⑥ SQL 方言下拉 / ⑦ 语言名 + B1 误标 / ⑨ 类型表 label 汉化③）
 *
 * ## 判据出处
 *
 * - 需求 `文档记录/需求文档/v1.6.1.md` **AC-74** 的 ⑥⑦⑨（2026-10-15/16 用户实测追加）；
 * - 设计 `文档记录/设计文档/v1.6.1.md` **§15.2.5**（B1：纯文本显示成「CSV」）、
 *   **§15.2.6**（汉化③ + 派生链）、**§15.2.7**（SQL 方言显示名）；
 * - 用户裁决 V-5（汉化范围 ①+②+③）。
 *
 * ## 为什么这三条能一起判（并且**失败得起来**）
 *
 * 三条判据的输入都是 `config/scratch-types.ts` 的**唯一真相**：
 *   1. `TYPES[].label`（唯一手写清单，派生 manifest 的来源）；
 *   2. `languageLabel(language, ext)` / `resolveTypeForPath(path).label`（状态栏与工具栏共用）；
 *   3. `sqlDialectLabel(id)`（方言下拉的可见文本）。
 * 因此用例全部按"逐项遍历 + 判定"写，**没有一条存在性断言** ——
 * 把 `label` 改回英文、把 `languageLabel` 退回"只按 language"、或让方言表漏一项，
 * 都会有对应的用例变红。
 *
 * ## SQL 方言"全集"从哪来（**关键：不是硬编码**）
 *
 * `api.formatters.sqlDialects` 是**运行期数据**（离线包里的 `sql-formatter` 内置表），
 * 源码里没有字面量。这里用 `src/test/cm-bundle.ts` 的 `loadCmBundle()`（与 AC-32 同一手法）
 * 取**真实现**，再逐项断言显示名 —— 离线包升级后方言表一变，用例立刻拦下来
 * （这正是"映射表覆盖面必须等于运行时全集"的可判定形式）。
 * 开发记录里登记的实测数量 = **21**。
 */

import { describe, expect, it } from 'vitest';
import {
  EXTENSIONS,
  LANGUAGE_LABELS,
  LANGUAGES,
  PROPER_NOUN_DIALECTS,
  TYPES,
  languageLabel,
  resolveTypeForPath,
  sqlDialectLabel,
} from '../scratch-types';
import type { ScratchLanguage } from '../scratch-types';
import { loadCmBundle } from '../../test/cm-bundle';

/** AC-74⑨ 的字面判据：`含空格 且 全部由 ASCII 字母 / 空格 / 连字符构成` */
const ENGLISH_PHRASE_LABEL = /^[A-Za-z -]+$/;

/** AC-74⑥ 的"含中日韩字符"判据 */
const CJK = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;

describe('AC-74⑦ 语言名（含 B1 误标修正：`.txt` 不得显示「CSV」、`.csv`/`.tsv` 可区分）', () => {
  it('`.txt` / `.csv` / `.tsv` 三个显示名互不相同，且都不等于裸语言 id', () => {
    const txt = resolveTypeForPath('D:/tmp/a.txt').label;
    const csv = resolveTypeForPath('D:/tmp/a.csv').label;
    const tsv = resolveTypeForPath('D:/tmp/a.tsv').label;

    // 现状缺陷：`languageLabel('text')` 按"首个匹配 TYPES 项"取值 ⇒ 三者**全是**「CSV」
    expect(txt, '.txt 不得显示「CSV」（B1 误标）').not.toBe('CSV');
    expect(tsv, '.tsv 必须与 .csv 可区分').not.toBe(csv);
    expect(txt).not.toBe(tsv);
    expect(new Set([txt, csv, tsv]).size, '三者必须互不相同').toBe(3);
    // 显示名必须是中文（不得回退成裸语言 id `text`）
    expect(CJK.test(txt), `.txt 的显示名「${txt}」不含中文`).toBe(true);
    expect(csv).toBe('CSV');
    expect(tsv).toBe('TSV');
  });

  it('`language:\'text\'` 的全部 8 个扩展名逐个不再误标为「CSV」', () => {
    // 证据：`config/scratch-types.ts` 的 TYPES 里这 8 项都是 `language:"text"`
    const textExts = TYPES.filter((entry) => entry.language === 'text').map((entry) => entry.ext);
    expect(textExts, '夹具前提：language=text 的扩展名集合').toEqual([
      'csv',
      'tsv',
      'toml',
      'ini',
      'conf',
      'properties',
      'log',
      'txt',
    ]);
    for (const ext of textExts) {
      const label = languageLabel('text', ext);
      expect(label, `languageLabel('text', '${ext}') 回退成了裸 id`).not.toBe('text');
      // 只有 csv / tsv 两项**应该**叫 CSV / TSV；其余都不得是「CSV」
      if (ext !== 'csv') {
        expect(label, `.${ext} 仍被误标为「CSV」`).not.toBe('CSV');
      }
      expect(label).toBe(resolveTypeForPath(`D:/tmp/x.${ext}`).label);
    }
  });

  it('非 `text` 语言按语言名取值（与扩展名无关），且 20 个取值都有中文/专有名词显示名', () => {
    expect(languageLabel('python')).toBe('Python');
    expect(languageLabel('python', 'txt')).toBe('Python'); // ext 不覆盖非 text 语言
    expect(languageLabel('diff')).toBe('差异对比');
    expect(languageLabel('shell')).toBe('Shell 脚本');
    // 覆盖集必须含 `ScratchLanguage` 的全部取值（少一个就 tsc 报缺键，这里再兜一层运行期判据）
    for (const language of LANGUAGES) {
      const label = LANGUAGE_LABELS[language as ScratchLanguage];
      expect(label, `语言 ${language} 缺显示名`).toBeTruthy();
      expect(label, `语言 ${language} 的显示名是裸 id`).not.toBe(language);
    }
    // 未知语言不编造：回退原文
    expect(languageLabel('not-a-language')).toBe('not-a-language');
  });

  it('`ScratchEditorStatusBar` 的取值路径（`resolveTypeForPath`）对 `.json` / `.py` 各自正确', () => {
    expect(resolveTypeForPath('D:/tmp/a.json').label).toBe('JSON');
    expect(resolveTypeForPath('D:/tmp/a.py').label).toBe('Python');
    expect(resolveTypeForPath('D:/tmp/a.md').label).toBe('Markdown');
  });

  it('`EXTENSIONS` 与 `TYPES` 的 label 同源（改一处不会只改一半）', () => {
    EXTENSIONS.forEach((info, index) => {
      expect(info.label, `EXTENSIONS[${index}] 与 TYPES 不同源`).toBe(TYPES[index].label);
    });
  });
});

describe('AC-74⑨ 类型表 label 不得是英文短语（汉化③）', () => {
  it('逐项断言：0 项 label 匹配「含空格且全为 ASCII 字母/空格/连字符」', () => {
    const bad = TYPES.filter(
      (entry) => ENGLISH_PHRASE_LABEL.test(entry.label) && entry.label.includes(' '),
    ).map((entry) => `${entry.ext} → ${entry.label}`);
    expect(bad, `仍有英文短语式 label：${bad.join('、')}`).toEqual([]);
  });

  it('本轮改的 4 个 label 逐项落在中文（`jsonc` / `properties` / `diff` / `patch`）', () => {
    const labelOf = (ext: string): string => {
      const entry = TYPES.find((item) => item.ext === ext);
      expect(entry, `TYPES 缺 ${ext}`).toBeTruthy();
      return entry?.label ?? '';
    };
    expect(labelOf('jsonc')).toBe('带注释的 JSON');
    expect(labelOf('properties')).toBe('属性文件');
    expect(labelOf('diff')).toBe('差异');
    expect(labelOf('patch')).toBe('补丁');
    for (const ext of ['jsonc', 'properties', 'diff', 'patch']) {
      expect(CJK.test(labelOf(ext)), `${ext} 的 label 不含中文`).toBe(true);
    }
  });

  it('专有名词按 AC-74⑧ 明确保留（未被顺手英文化，也未被改成中文）', () => {
    const properNouns: Record<string, string> = {
      json: 'JSON',
      csv: 'CSV',
      tsv: 'TSV',
      md: 'Markdown',
      xml: 'XML',
      html: 'HTML',
      sql: 'SQL',
      yaml: 'YAML',
    };
    for (const [ext, expected] of Object.entries(properNouns)) {
      const entry = TYPES.find((item) => item.ext === ext);
      expect(entry?.label, `${ext} 的专有名词显示名被改了`).toBe(expected);
    }
    // 条目数不变：派生链的单一真相（AC-48a）
    expect(TYPES).toHaveLength(34);
  });
});

describe('AC-74⑥ SQL 方言下拉显示名（覆盖面 = 运行时 `formatters.sqlDialects` 全集）', () => {
  /** 运行时全集（真离线包；**不是**源码字面量，见文件头） */
  const dialectIds: readonly string[] = loadCmBundle().formatters.sqlDialects;

  it('运行时方言全集非空，且逐项：显示名不等于裸 id，且（含中日韩字符 或 是白名单专有名词）', () => {
    expect(dialectIds.length, '运行时方言表为空（离线包异常）').toBeGreaterThan(0);
    const offenders: string[] = [];
    for (const id of dialectIds) {
      const label = sqlDialectLabel(id);
      const key = id.trim().toLowerCase();
      const properNoun = PROPER_NOUN_DIALECTS.has(key);
      if (label === id) {
        offenders.push(`${id} → ${label}（裸回退，违反⑥"不得等于 id"）`);
        continue;
      }
      // ⑥ 的"至少含 1 个中日韩字符"：AC-74⑧ 明确把专有名词豁免在外
      // （`PostgreSQL` / `SQLite` 这类**没有**中文写法；豁免后用"≠ id"这一条兜住"确实配了显示名"）
      if (!CJK.test(label) && !properNoun) {
        offenders.push(`${id} → ${label}（既无中日韩字符，也非白名单专有名词）`);
      }
      if (!label.includes('（')) {
        offenders.push(`${id} → ${label}（格式不是「名称（id）」）`);
      }
    }
    expect(
      offenders,
      `以下方言的显示名不满足 AC-74⑥/⑧（共 ${dialectIds.length} 项）：${offenders.join('、')}`,
    ).toEqual([]);
    // 至少要有中文名的那些项（非专有名词）确实用了中文
    for (const id of ['plsql', 'tsql']) {
      expect(CJK.test(sqlDialectLabel(id)), `${id} 应给中文名`).toBe(true);
    }
  });

  it('映射表覆盖面 == 运行时全集（少一项即红；实测 21 项，见开发记录）', () => {
    const uncovered = dialectIds.filter((id) => {
      const key = id.trim().toLowerCase();
      return !PROPER_NOUN_DIALECTS.has(key) && sqlDialectLabel(id) === id;
    });
    expect(uncovered, `未登记的方言 id：${uncovered.join('、')}`).toEqual([]);
    expect(dialectIds).toHaveLength(21);
    // 逐项给一个可读的锚点：专有名词保留原名 + （id）
    for (const [id, expected] of [
      ['mysql', 'MySQL（mysql）'],
      ['postgresql', 'PostgreSQL（postgresql）'],
      ['plsql', 'Oracle 过程语言（plsql）'],
      ['tsql', 'T-SQL 方言（tsql）'],
      ['sqlite', 'SQLite（sqlite）'],
    ] as const) {
      expect(sqlDialectLabel(id), `${id} 的显示名口径漂移`).toBe(expected);
    }
  });

  it('[负向对照] 把某个方言从白名单与中文表里摘掉后，显示名会裸回退成 id（证明上面的断言有区分度）', () => {
    // `sqlDialectLabel` 的"未知 id"分支：**不编造**、回退原文 ⇒ 这正是 AC-74⑥ 的失败形态
    expect(sqlDialectLabel('some-brand-new-dialect')).toBe('some-brand-new-dialect');
  });

  it('大小写与空白归一（离线包给的都是小写 id，但下拉的键不该依赖这一点）', () => {
    expect(sqlDialectLabel('MySQL')).toBe('MySQL（MySQL）');
    // 空白只用于查表，显示名里保留调用方给的原文（不悄悄改写用户看到的东西）
    expect(sqlDialectLabel('  mysql  ')).toBe('MySQL（  mysql  ）');
  });
});
