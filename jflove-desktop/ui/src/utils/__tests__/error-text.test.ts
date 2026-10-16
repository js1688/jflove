/**
 * 「异常 → 用户可见文案」**唯一真相叶子模块**单测 —— v1.6.1 Phase 6 收尾轮（缺陷 L-7）
 *
 * ## 判据出处
 *
 * - Phase 5 审查轻微项 **L-7**：`describeError` 曾在五个文件里各写一份
 *   （`utils/scratch/shortcut-runtime.ts:75`、`stores/shortcut-store.ts:83`、
 *   `stores/scratch-store.ts:118`、`components/scratch/FileAssocSettings.tsx:37`、
 *   `components/scratch/ScratchWorkspace.tsx:46`）；
 * - 用户裁决原文：「L-7 `describeError` ×5 → 收敛为 1 处共享实现（**纯重构，断言不变**）」
 *   —— 所以本文件锁的是**抽取前的实际行为**，不是"理想行为"；
 * - 安全口径 `AGENTS.md` **§9.13**：错误文案不得 echo 完整路径 / 文件内容片段；
 *   实现侧依据是设计 v1.6.1 §7.4（文案只用文件名 / 数字，不含目录）。
 *
 * ## 用例分工
 *
 * | # | 判据 | 失效表现 |
 * |---|------|----------|
 * | ① | `Error` 取 `message`；`message` 为**空串**时落兜底 | 空串被当成有效文案 ⇒ 弹一个**空白** toast |
 * | ② | 非 `Error` 的 `{code}` / `{reason}` / 字符串 / 数字 / 数组一律落兜底 | 试图"从任意对象里挖文案" ⇒ 用户看见 `[object Object]` |
 * | ③ | `null` / `undefined` / 空串 / 函数 / `Symbol` 兜底不抛 | 兜底路径抛错 ⇒ 二次异常盖住原始错误 |
 * | ④ | **兜底文案逐字等于抽取前五处原值** | 收敛时"顺手统一措辞" ⇒ **用户可见文案被悄悄改掉**（L-7 的明确禁区） |
 * | ⑤ | 输出**不含完整路径 / 文件内容片段**（§9.13） | 路径或正文泄漏到界面上 |
 * | ⑥ | **静态扫描**：叶子模块源码**不含任何 import / require / 动态 import** | 叶子长出依赖 ⇒ `stores/**` 与 `components/**` 之间随时能形成新环（同 R2 的处置口径） |
 *
 * ## 为什么 ④ 用的是"逐字原值"而不是"看着合理"
 *
 * L-7 分三类的结论是：五份的**取 `message` 判据逐字相同**，差别只在**兜底文案**
 * （`'操作失败'` ×2、`'快捷键偏好操作失败'`、`'保存失败'`、`'文件关联操作失败'`）。
 * 这是"**仅风格不同**"里的特例 —— 代码结构等价，但**兜底是给用户看的字符串**，
 * 一旦统一就等于改文案。因此收敛方案是"同一算法 + 兜底参数"，本用例把四个字面量钉死。
 *
 * ## ⑤ 的边界（必须说清，避免用例自欺）
 *
 * 本模块是**纯透传**：`Error.message` 里如果上游塞了完整路径，本模块**不负责剥离**
 * （那会改变既有行为，属行为变更）。所以 ⑤ 锁的是两件事：
 * - **兜底文案**（本模块自己产出的字面量）天然无路径 / 无扩展名 / 无换行；
 * - 上游已按 §7.4 洗过的 message（"目录不存在"这类）原样通过，不被本模块加料。
 *
 * 上游剥离责任的落点在 Python 桥与 store —— 见 `AGENTS.md §9.13` 与设计 §7.4。
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { ERROR_FALLBACK, describeErrorWithFallback } from '../error-text';

/** 抽取前五处的兜底文案原值（逐字，**不得**改写） */
const ORIGINAL_FALLBACKS = [
  '操作失败',
  '快捷键偏好操作失败',
  '保存失败',
  '文件关联操作失败',
] as const;

/** 读同仓库源码文本（用 `import.meta.url` 定位，不依赖 `process.cwd()`） */
function readSource(relativeToThisFile: string): string {
  return readFileSync(fileURLToPath(new URL(relativeToThisFile, import.meta.url)), 'utf8');
}

/** Windows 绝对路径 / UNC / POSIX 多段路径的形态判据 */
const FULL_PATH_PATTERNS: readonly RegExp[] = [
  /[A-Za-z]:[\\/]/, // 盘符：C:\ 或 D:/
  /\\\\[^\\]/, // UNC：\\server
  /(?:^|[\s"'`(=])\/(?:[^/\s]+\/)+/, // POSIX 多段：/home/u/secret.txt
];

/** 文件内容片段的形态判据（本模块不该自己造出这些形状） */
const CONTENT_LIKE_PATTERNS: readonly RegExp[] = [
  /[\r\n]/, // 换行 ⇒ 像是正文片段而不是简短文案
  /[{}<>]/, // 结构化片段
];

describe('error-text（L-7 共享「异常 → 文案」叶子模块）', () => {
  describe('① Error 实例取 message', () => {
    it('普通 Error：原样取 message（不包装、不改写）', () => {
      expect(describeErrorWithFallback(new Error('目录不存在'), ERROR_FALLBACK.OPERATION)).toBe(
        '目录不存在',
      );
    });

    it('Error 子类（TypeError / RangeError）：同样取 message', () => {
      expect(describeErrorWithFallback(new TypeError('参数类型不对'), ERROR_FALLBACK.SAVE)).toBe(
        '参数类型不对',
      );
      expect(
        describeErrorWithFallback(new RangeError('数值越界'), ERROR_FALLBACK.FILE_ASSOC),
      ).toBe('数值越界');
    });

    it('自定义 message 的 Error 子类：取它自己的 message', () => {
      class EditorError extends Error {}
      expect(
        describeErrorWithFallback(new EditorError('文件已在别处被修改'), ERROR_FALLBACK.SAVE),
      ).toBe('文件已在别处被修改');
    });

    it('message 为**空串** ⇒ 落兜底（抽取前判据是 `&& error.message` 真值判定）', () => {
      expect(describeErrorWithFallback(new Error(''), ERROR_FALLBACK.OPERATION)).toBe('操作失败');
      expect(describeErrorWithFallback(new Error(''), ERROR_FALLBACK.SHORTCUT)).toBe(
        '快捷键偏好操作失败',
      );
    });

    it('message 为纯空白串 ⇒ 非空 ⇒ **原样通过**（真值判定不看内容，保持既有行为）', () => {
      // 这一条是"记录既有行为"而不是"认可它"：改掉它属行为变更，本版不做。
      expect(describeErrorWithFallback(new Error(' '), ERROR_FALLBACK.OPERATION)).toBe(' ');
    });

    it('message 中的中文长句 / 标点原样保留（不做截断与转义）', () => {
      const message = '该文件被判定为只读，未写入';
      expect(describeErrorWithFallback(new Error(message), ERROR_FALLBACK.SAVE)).toBe(message);
    });
  });

  describe('② 非 Error 形态一律落兜底', () => {
    it('带 code / reason 的普通对象 ⇒ 落兜底（不挖属性）', () => {
      expect(
        describeErrorWithFallback({ code: 'EACCES', reason: 'read_only' }, ERROR_FALLBACK.SAVE),
      ).toBe('保存失败');
      expect(
        describeErrorWithFallback(
          { ok: false, reason: 'conflict', error: '文件已在别处被修改' },
          ERROR_FALLBACK.FILE_ASSOC,
        ),
      ).toBe('文件关联操作失败');
    });

    it('字符串输入 ⇒ 落兜底（字符串不是 Error；调用方传的字面量走同一路径）', () => {
      // 就地取材：`scratch-store.ts` 的 `describeError(result?.error ?? '保存失败')`
      // 在 `result?.error` 缺省时正是把字符串喂进来 ⇒ 必须落兜底而不是回自己的 message。
      expect(describeErrorWithFallback('保存失败', ERROR_FALLBACK.SAVE)).toBe('保存失败');
      expect(describeErrorWithFallback('打开文件失败', ERROR_FALLBACK.OPERATION)).toBe('操作失败');
    });

    it('数字 / 布尔 / 数组 / 类 Error 的鸭子对象 ⇒ 落兜底', () => {
      const fallback = ERROR_FALLBACK.OPERATION;
      expect(describeErrorWithFallback(404, fallback)).toBe(fallback);
      expect(describeErrorWithFallback(0, fallback)).toBe(fallback);
      expect(describeErrorWithFallback(false, fallback)).toBe(fallback);
      expect(describeErrorWithFallback(['failed'], fallback)).toBe(fallback);
      // 鸭子对象：有 message 但不是 Error 实例（跨 realm 的失败形态）⇒ 抽取前也不算，维持原样
      expect(describeErrorWithFallback({ message: '看起来像错误' }, fallback)).toBe(fallback);
      expect(describeErrorWithFallback({ name: 'Error', message: '假的' }, fallback)).toBe(fallback);
    });
  });

  describe('③ null / undefined / 空串等兜底路径不抛', () => {
    it('null 与 undefined ⇒ 返回调用方给的兜底', () => {
      expect(describeErrorWithFallback(null, ERROR_FALLBACK.SHORTCUT)).toBe('快捷键偏好操作失败');
      expect(describeErrorWithFallback(undefined, ERROR_FALLBACK.SHORTCUT)).toBe(
        '快捷键偏好操作失败',
      );
    });

    it('空串 / 函数 / Symbol / 空对象 ⇒ 返回兜底且不抛', () => {
      const fallback = ERROR_FALLBACK.FILE_ASSOC;
      expect(describeErrorWithFallback('', fallback)).toBe(fallback);
      expect(describeErrorWithFallback(() => 'x', fallback)).toBe(fallback);
      expect(describeErrorWithFallback(Symbol('boom'), fallback)).toBe(fallback);
      expect(describeErrorWithFallback({}, fallback)).toBe(fallback);
      expect(describeErrorWithFallback([], fallback)).toBe(fallback);
    });

    it('兜底参数本身被原样返回（不做 trim / 前后缀加工）', () => {
      expect(describeErrorWithFallback(null, '  自定义兜底  ')).toBe('  自定义兜底  ');
      expect(describeErrorWithFallback(null, '')).toBe('');
    });
  });

  describe('④ 兜底文案逐字等于抽取前五处的原值（L-7 的"不得改文案"禁区）', () => {
    it('ERROR_FALLBACK 四项与原字面量逐字一致', () => {
      expect(ERROR_FALLBACK.OPERATION).toBe('操作失败');
      expect(ERROR_FALLBACK.SHORTCUT).toBe('快捷键偏好操作失败');
      expect(ERROR_FALLBACK.SAVE).toBe('保存失败');
      expect(ERROR_FALLBACK.FILE_ASSOC).toBe('文件关联操作失败');
    });

    it('对非 Error 输入，用同一兜底 ⇒ 输出等于该兜底（四个场景各验一次）', () => {
      const bogus = { code: 'ENOENT' };
      expect(describeErrorWithFallback(bogus, ERROR_FALLBACK.OPERATION)).toBe('操作失败');
      expect(describeErrorWithFallback(bogus, ERROR_FALLBACK.SHORTCUT)).toBe('快捷键偏好操作失败');
      expect(describeErrorWithFallback(bogus, ERROR_FALLBACK.SAVE)).toBe('保存失败');
      expect(describeErrorWithFallback(bogus, ERROR_FALLBACK.FILE_ASSOC)).toBe('文件关联操作失败');
    });

    it('四个兜底都在"抽取前原值"清单内（防止新增第五种措辞）', () => {
      for (const value of Object.values(ERROR_FALLBACK)) {
        expect(ORIGINAL_FALLBACKS).toContain(value);
      }
      expect(Object.values(ERROR_FALLBACK)).toHaveLength(ORIGINAL_FALLBACKS.length);
    });
  });

  describe('⑤ 文案不得含完整路径或文件内容片段（AGENTS.md §9.13）', () => {
    it('四个兜底文案都不含完整路径 / 文件名扩展名 / 换行 / 结构片段', () => {
      for (const text of Object.values(ERROR_FALLBACK)) {
        for (const pattern of FULL_PATH_PATTERNS) {
          expect(text).not.toMatch(pattern);
        }
        for (const pattern of CONTENT_LIKE_PATTERNS) {
          expect(text).not.toMatch(pattern);
        }
        // 没有文件扩展名形态（`.txt` / `.json` 等 ⇒ 像是带了文件名）
        expect(text).not.toMatch(/\.[A-Za-z0-9]{2,4}\b/);
      }
    });

    it('上游已洗过的 message（只保留必要信息）原样通过，不被本模块加料', () => {
      const washed = '目录不存在';
      const out = describeErrorWithFallback(new Error(washed), ERROR_FALLBACK.OPERATION);
      expect(out).toBe(washed);
      for (const pattern of FULL_PATH_PATTERNS) {
        expect(out).not.toMatch(pattern);
      }
    });

    it('本模块**不**替上游剥离 message 里的路径（透传是既有行为，不属本版改动面）', () => {
      // 记录既有行为：剥离责任在 Python 桥 / store（§9.13 与设计 §7.4），
      // 本模块若在这里加剥离逻辑，就是行为变更，会与"纯重构，断言不变"的裁决冲突。
      const leaked = new Error('无法写入 D:\\data\\secret.txt');
      expect(describeErrorWithFallback(leaked, ERROR_FALLBACK.SAVE)).toBe(
        '无法写入 D:\\data\\secret.txt',
      );
    });
  });

  describe('⑥ 静态扫描：叶子模块没有任何依赖边（防模块环回归）', () => {
    it('error-text.ts 源码不含 import / require / 动态 import（本模块只收 `it` 里的 expect）', () => {
      const source = readSource('../error-text.ts');
      // 顶部文档注释里出现的 `import` 字样是中文说明，故只看**行首**的语句级 import
      expect(source).not.toMatch(/^\s*import\b/m);
      expect(source).not.toMatch(/^\s*export\s+[^;]*\bfrom\b/m);
      expect(source).not.toMatch(/\brequire\s*\(/);
      expect(source).not.toMatch(/\bimport\s*\(/);
    });

    it('叶子模块确实导出了共享实现与四个兜底常量（避免"抽了但没人接"）', () => {
      const source = readSource('../error-text.ts');
      expect(source).toMatch(/export function describeErrorWithFallback\(/);
      expect(source).toMatch(/export const ERROR_FALLBACK = \{/);
    });

    it('5 个原调用方都已改为从共享模块取判据，且**不再本地定义** describeError', () => {
      const callers = [
        '../scratch/shortcut-runtime.ts',
        '../../stores/scratch-store.ts',
        '../../stores/shortcut-store.ts',
        '../../components/scratch/FileAssocSettings.tsx',
        '../../components/scratch/ScratchWorkspace.tsx',
      ] as const;
      for (const relative of callers) {
        const source = readSource(relative);
        // 旧定义形态（`function describeError(`）必须消失
        expect(source).not.toMatch(/function\s+describeError\s*\(/);
        // 且必须真的 import 了共享实现
        expect(source).toMatch(/describeErrorWithFallback/);
      }
    });
  });
});
