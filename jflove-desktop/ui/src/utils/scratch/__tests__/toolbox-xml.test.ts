/**
 * 工具箱第 10 项「XML 格式化 / 自研安全压缩」负向对照 —— Phase 6（v1.6.0 · AC-33）
 *
 * 判据出处（逐字符）：`文档记录/需求文档/v1.6.0.md` AC-33 原文 ——
 *   XML 输入 `<root><!--c--><a><![CDATA[ x < y ]]></a></root>`：
 *   **格式化**后仍含 `<!--c-->` 与 `<![CDATA[ x < y ]]>`，且 CDATA 内文本逐字符不变；
 *   **压缩**后可用标准 XML 解析器解析成功（判据：`python -c "import xml.etree.ElementTree as ET;
 *   ET.fromstring(open('out.xml',encoding='utf-8').read())"` **退出码 0**），且
 *   ① **非纯空白**文本节点的值不变；② **纯空白文本节点不得被删除**（如 `<a> </a>` 压缩后仍含该空格）；
 *   ③ **多行混排文本不得粘词**（`This is <b>some</b> content.` 压缩后词间空格保留）`[负向对照]`
 *   （⚠ 需求原文自带注记：按 R-6 实测修正，原措辞"文本节点内容不变"在正常缩进文档上会**假阳性失败**）
 *
 * 实现口径（`文档记录/设计文档/v1.6.0.md` §4.1.6 第 10 项 + R-6 定稿参数）：
 *   格式化 = `xml-formatter`（`strictMode:true` + `throwOnFailure:true` + `collapseContent:true`，
 *   库会丢 BOM ⇒ 调用方用 `restoreBomForFormatted` 补回）；
 *   压缩 = **自研**（不使用库的 `minify`，理由见 `toolbox.ts:579-597`，因为库会吃掉纯空白文本节点、会粘词）。
 *
 * 执行方式：格式化走离线包真实现（`window.__JFLOVE_CM__.formatters.xml`，即 `xml-formatter@3.7.0`）；
 * 压缩与预检走本仓库纯函数；标准解析器判据走**系统 Python 的 `xml.etree.ElementTree`**
 * （`execFileSync`，退出码非 0 即抛错）。Python 解释器解析顺序与实测结果见 `resolvePython()`。
 *
 * `[负向对照]` 锚点：① 直接用 `xml-formatter` 会吃掉 `<a> </a>` 里的空格（本文件把它固化成对照用例）；
 * ② 压缩"没有改动任何字符"必须被显式记录，避免用例在"函数其实是空操作"时依然全绿而无人察觉。
 */
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  DEFAULT_XML_FORMAT_OPTIONS,
  checkXmlFormattable,
  checkXmlMinifiable,
  hasBom,
  restoreBomForFormatted,
  stripBom,
  withBom,
  xmlSafeMinify,
} from '../toolbox';
import { loadCmFormatters } from '../../../test/cm-bundle';

/** 需求 AC-33 的原文样本 */
const AC33_SAMPLE = '<root><!--c--><a><![CDATA[ x < y ]]></a></root>';
/** 需求 ③（任务书 ⑤）点名的粘词对抗样本（多行混排版） */
const MULTILINE_MIXED = '<root>\n  <p>\n    This is <b>some</b> content.\n  </p>\n</root>';
/** 需求 ④ 点名的纯空白文本节点样本 */
const ONLY_SPACE = '<root><a> </a></root>';

const formatters = loadCmFormatters();

/* ---------------------------------------------------------------------------
 * Python 解释器：需求 AC-33 指定"标准 XML 解析器"判据。
 * 优先系统 PATH 上的 `python`，不可用时退回桌面端 venv（`venv-win`）；两者都不可用 ⇒ 明确报错。
 * 实测（2026-10-15，本机 Node v24.18.0）：系统 `python` 可用，版本 Python 3.13.14。
 * ------------------------------------------------------------------------- */
interface PythonInterpreter {
  command: string;
  source: string;
}

const PYTHON_CANDIDATES: readonly PythonInterpreter[] = [
  { command: 'python', source: '系统 PATH 上的 python' },
  {
    command: 'D:\\github\\jflove\\jflove-desktop\\venv-win\\Scripts\\python.exe',
    source: '桌面端 venv-win',
  },
];

let resolvedPython: PythonInterpreter | null = null;

function resolvePython(): PythonInterpreter {
  if (resolvedPython) return resolvedPython;
  for (const candidate of PYTHON_CANDIDATES) {
    try {
      execFileSync(candidate.command, ['--version'], { encoding: 'utf8', stdio: 'pipe' });
      resolvedPython = candidate;
      break;
    } catch {
      // 该候选不可用，试下一个（两者都不可用时在下面统一报错）
    }
  }
  if (!resolvedPython) {
    throw new Error('未找到可用的 Python 解释器：AC-33 的标准 XML 解析器判据无法执行');
  }
  console.warn(`[AC-33] Python 判据使用：${resolvedPython.source}（${resolvedPython.command}）`);
  return resolvedPython;
}

/** 需求 AC-33 原文给出的判据脚本（逐字照抄） */
const ET_PARSE_SCRIPT =
  'import xml.etree.ElementTree as ET,sys; ET.fromstring(open(sys.argv[1],encoding="utf-8").read())';

/**
 * 需求 AC-33 ① 的可执行判据：两份文档里「**非纯空白**文本节点」的值必须逐字符集合相等。
 * 用 `.strip()` 只做**筛选**（留纯空白文本节点在外），保留的值本身不做任何规范化。
 */
const ET_TEXTS_EQUAL_SCRIPT = [
  'import xml.etree.ElementTree as ET,sys',
  'def texts(p): return [x for x in ET.fromstring(open(p,encoding="utf-8").read()).itertext() if x.strip()]',
  'sys.exit(0 if texts(sys.argv[1]) == texts(sys.argv[2]) else 1)',
].join('\n');

/** 同步跑一段 Python：退出码非 0 ⇒ `execFileSync` 抛错（这就是"退出码 0"判据的执行体） */
function runPython(script: string, args: readonly string[]): void {
  const python = resolvePython();
  execFileSync(python.command, ['-c', script, ...args], { encoding: 'utf8', stdio: 'pipe' });
}

/** 在系统临时目录里落文件、跑断言、**无论成败都清理**（仓库工作区不留垃圾） */
function withTempXmlFiles(
  files: ReadonlyArray<{ name: string; content: string }>,
  run: (paths: readonly string[]) => void,
): void {
  const dir = mkdtempSync(join(tmpdir(), 'jflove-ac33-'));
  try {
    const paths = files.map((file) => {
      const path = join(dir, file.name);
      writeFileSync(path, file.content, 'utf8');
      return path;
    });
    run(paths);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/** 压缩成功路径的统一取数：被拒 / 无 output 都直接抛错，避免断言在 `undefined` 上"假通过" */
function minifyOrThrow(source: string): string {
  const result = xmlSafeMinify(source);
  if (!result.ok) throw new Error(`压缩被拒绝：${result.reason ?? '(无理由)'}`);
  if (typeof result.output !== 'string') throw new Error('压缩返回 ok:true 却没有 output');
  return result.output;
}

describe('AC-33 ① 格式化保住注释与 CDATA（离线包真实现）', () => {
  it('预检放行，且格式化输出仍含 `<!--c-->` 与 `<![CDATA[ x < y ]]>`', () => {
    expect(checkXmlFormattable(AC33_SAMPLE)).toEqual({ ok: true, warnings: [] });
    const formatted = formatters.xml(AC33_SAMPLE, DEFAULT_XML_FORMAT_OPTIONS);
    expect(formatted).toContain('<!--c-->');
    expect(formatted).toContain('<![CDATA[ x < y ]]>');
  });

  it('CDATA 内文本逐字符不变（含两侧空格，不得被 trim / 折叠）', () => {
    const formatted = formatters.xml(AC33_SAMPLE, DEFAULT_XML_FORMAT_OPTIONS);
    const cdata = /<!\[CDATA\[([\s\S]*?)\]\]>/.exec(formatted);
    expect(cdata?.[1]).toBe(' x < y ');
  });

  it('【负向对照】格式化不得产出非法 XML：输出同样能过 `ET.fromstring`', () => {
    const formatted = formatters.xml(AC33_SAMPLE, DEFAULT_XML_FORMAT_OPTIONS);
    withTempXmlFiles([{ name: 'formatted.xml', content: formatted }], (paths) => {
      expect(() => runPython(ET_PARSE_SCRIPT, [paths[0]])).not.toThrow();
    });
  });
});

describe('AC-33 ② 压缩后可用标准 XML 解析器解析（`ET.fromstring` 退出码 0）', () => {
  it('需求原文样本：压缩输出写到临时文件后解析退出码 0', () => {
    const output = minifyOrThrow(AC33_SAMPLE);
    withTempXmlFiles([{ name: 'out.xml', content: output }], (paths) => {
      expect(() => runPython(ET_PARSE_SCRIPT, [paths[0]])).not.toThrow();
    });
  });

  it('多行混排样本同样可解析（含注释、CDATA 与行内元素的组合）', () => {
    const output = minifyOrThrow(MULTILINE_MIXED);
    withTempXmlFiles([{ name: 'out.xml', content: output }], (paths) => {
      expect(() => runPython(ET_PARSE_SCRIPT, [paths[0]])).not.toThrow();
    });
  });
});

describe('AC-33 ① 非纯空白文本节点的值不变（需求 ① / 任务书 ③）', () => {
  it('压缩前后"非纯空白文本节点"的值逐字符相同（`ET.itertext` 交叉校验，退出码 0）', () => {
    const mixedOutput = minifyOrThrow(MULTILINE_MIXED);
    const cdataOutput = minifyOrThrow(AC33_SAMPLE);
    withTempXmlFiles(
      [
        { name: 'mixed-before.xml', content: MULTILINE_MIXED },
        { name: 'mixed-after.xml', content: mixedOutput },
        { name: 'cdata-before.xml', content: AC33_SAMPLE },
        { name: 'cdata-after.xml', content: cdataOutput },
      ],
      (paths) => {
        expect(() => runPython(ET_TEXTS_EQUAL_SCRIPT, [paths[0], paths[1]])).not.toThrow();
        expect(() => runPython(ET_TEXTS_EQUAL_SCRIPT, [paths[2], paths[3]])).not.toThrow();
      },
    );
  });

  it('文本节点里的多空格原样保留（不得被折叠成一个空格）', () => {
    const output = minifyOrThrow('<root><a>hello   world</a></root>');
    expect(output).toContain('<a>hello   world</a>');
    expect(output).not.toContain('<a>hello world</a>');
  });

  it('多行混排里的行内文本片段逐字符保留（含其前后的换行与缩进空白）', () => {
    const output = minifyOrThrow(MULTILINE_MIXED);
    expect(output).toContain('\n    This is ');
    expect(output).toContain(' content.\n  ');
  });
});

describe('AC-33 ② 纯空白文本节点不得被删除（需求 ② / 任务书 ④）', () => {
  it('预检必须拒绝，并给出精确的位置警告（路径 / 标签 / 空白长度，不含内容原文）', () => {
    const guard = checkXmlMinifiable(ONLY_SPACE);
    expect(guard.ok).toBe(false);
    expect(guard.reason).toContain('纯空白');
    expect(guard.warnings).toEqual([{ path: '/root/a', tag: 'a', length: 1 }]);
  });

  it('压缩随之整体拒绝：不返回任何 output ⇒ 调用方只能保留原文，那个空格不可能被静默删掉', () => {
    const result = xmlSafeMinify(ONLY_SPACE);
    expect(result.ok).toBe(false);
    expect(result.output).toBeUndefined();
    expect(result.warnings).toEqual([{ path: '/root/a', tag: 'a', length: 1 }]);
  });

  it('【负向对照】直接用 `xml-formatter` 会吃掉这个空格 ⇒ 这正是自研预检存在的理由', () => {
    // R-6 实测结论（设计 §4.1.6 第 10 项）：库把 `<a> </a>` 变成 `<a></a>`。
    // 这条用例把"库不安全"的事实钉住；若哪天库被换掉/修好，它会失败并提醒复核预检是否还需要。
    expect(formatters.xml(ONLY_SPACE, DEFAULT_XML_FORMAT_OPTIONS)).toBe('<root>\n  <a></a>\n</root>');
  });

  it('`xml:space="preserve"` 子树内的纯空白不触发拒绝（子树空白由调用方自行保留）', () => {
    expect(checkXmlMinifiable('<root xml:space="preserve"><a> </a></root>').ok).toBe(true);
  });
});

describe('AC-33 ③ 多行混排不得粘词（需求 ③ / 任务书 ⑤）', () => {
  it('单行混合内容：`This is <b>some</b> content.` 词间空格保留', () => {
    const inline = '<root><p>This is <b>some</b> content.</p></root>';
    const output = minifyOrThrow(inline);
    expect(output).toContain('This is <b>some</b> content.');
    expect(output).not.toContain('Thisis');
    expect(output).not.toContain('</b>content');
  });

  it('多行混排：既不许粘词，也不许把行内空白折叠掉', () => {
    const output = minifyOrThrow(MULTILINE_MIXED);
    expect(output).toContain('This is <b>some</b> content.');
    expect(output).not.toContain('Thisis');
    expect(output).not.toContain('</b>content');
    // 逐字符锚点：行内文本前后的换行与缩进空白都在
    expect(output).toContain('\n    This is <b>some</b> content.\n  ');
  });
});

describe('AC-33 BOM 与非法输入（辅助判据）', () => {
  it('`hasBom` / `stripBom` / `withBom` 的边界行为', () => {
    expect(hasBom('\uFEFF<a/>')).toBe(true);
    expect(hasBom('<a/>')).toBe(false);
    expect(stripBom('\uFEFF<a/>')).toBe('<a/>');
    expect(stripBom('<a/>')).toBe('<a/>');
    expect(withBom('<a/>', true)).toBe('\uFEFF<a/>');
    // `withBom(text, false)` 是"不加"，**不是**"去掉"
    expect(withBom('\uFEFF<a/>', false)).toBe('\uFEFF<a/>');
  });

  it('`restoreBomForFormatted`：原串有 BOM 才补回（库会丢 BOM）', () => {
    expect(restoreBomForFormatted('\uFEFF<a/>', '<a/>')).toBe('\uFEFF<a/>');
    expect(restoreBomForFormatted('<a/>', '<a/>')).toBe('<a/>');
  });

  it('自研压缩保留 BOM 形态（压缩不改编码形态）', () => {
    const output = minifyOrThrow('\uFEFF<root><a>1</a></root>');
    expect(hasBom(output)).toBe(true);
    expect(stripBom(output)).toBe('<root><a>1</a></root>');
  });

  it('标签未配对的 XML：拒绝压缩，且提示不含文档内容（安全宪法 §9.14）', () => {
    const result = checkXmlMinifiable('<root><a>1</root>');
    expect(result.ok).toBe(false);
    expect(result.reason).toBe('XML 结构不完整（标签未配对），已拒绝压缩');
    expect(result.reason).not.toContain('root');
    expect(result.warnings).toEqual([]);
  });
});

describe('AC-33 已知不一致（it.fails：Phase 6 只登记、**不**改实现）', () => {
  it.fails('需求 ② 的字面口径「`<a> </a>` **压缩后仍含该空格**」：实现改为"整体拒绝压缩"⇒ 根本不存在 output', () => {
    // 实现取舍（`toolbox.ts:814-846`）：宁可拒绝，也不产出"删掉了内容"的结果。
    // 需求原文假设"会产出一个仍含空格的压缩结果"，与实现的安全策略不同口径 ⇒ 本条按 it.fails 登记。
    expect(xmlSafeMinify(ONLY_SPACE).output).toContain(' ');
  });

  it.fails('设计 §4.1.6 第 10 项口径「压缩 = 只删元素之间空白」：实测一个字符都没删', () => {
    // 根因（`ui/src/utils/scratch/toolbox.ts:795,799`）：`serializeElement` 只在
    // `!meaningful`（父元素**没有**任何有意义子节点）时才丢纯空白文本节点，
    // 而这恰好是 `checkXmlMinifiable`（`:779-784`）已经**整体拒绝**的情形 ⇒ 删除分支不可达。
    // 要真删元素间缩进空白，判据应改成"父元素**有**有意义子节点、且非空白子节点全是元素"（混合内容除外）。
    const source = '<root>\n  <a>1</a>\n  <b>2</b>\n</root>';
    expect(xmlSafeMinify(source).output).toBe('<root><a>1</a><b>2</b></root>');
  });

  it('现状底片（此条必过）：预检通过时压缩**等价于原样回写**', () => {
    const source = '<root>\n  <a>1</a>\n  <b>2</b>\n</root>';
    expect(xmlSafeMinify(source).output).toBe(source);
  });
});
