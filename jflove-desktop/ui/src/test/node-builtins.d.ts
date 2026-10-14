/**
 * `node:*` 内置模块的**最小**环境声明 —— Phase 6（v1.6.0）
 *
 * ## 为什么需要这个文件
 *
 * `ui/package.json` 的 devDependencies 里**没有** `@types/node`，而本任务的硬约束是
 * 「**不新增任何 npm 依赖**」（同设计文档 §6.1.1 DS-7：sql-formatter / xml-formatter 只走离线包）。
 * 但 `tsconfig.json` 的 `include: ["src"]` 会把 `src/test/**` 一起交给 `tsc --noEmit`
 * （`npm run build` 的第一步），于是测试辅助文件里一旦出现 `import ... from 'node:fs'`，
 * 缺少声明就会报 TS2307「Cannot find module 'node:fs' or its corresponding type declarations」。
 *
 * 所以这里**只为测试真正用到的成员**补最小声明，而不是把整份 `@types/node` 拉进依赖树。
 * 运行期不需要这些声明：vitest 跑在 Node 上，`node:*` 是真实存在的模块。
 *
 * ⚠ **维护提示**：将来若正式引入 `@types/node`，**必须删除本文件** ——
 * 两份声明同时存在会产生同名模块的重复导出 / 类型冲突。
 *
 * 依据：AC-32 / AC-33 的执行方式（真调离线包 + 标准 XML 解析器交叉校验）由任务书指定，
 * 而离线包与临时文件的读取只能经 Node 内置模块完成。
 */

declare module 'node:fs' {
  /** 读文本文件（测试只读 UTF-8） */
  export function readFileSync(path: string, encoding: 'utf8'): string;
  /** 写文本文件（AC-33 ② 的临时 XML） */
  export function writeFileSync(path: string, data: string, encoding?: 'utf8'): void;
  /** 建唯一临时目录（AC-33 ② 不往仓库里落垃圾） */
  export function mkdtempSync(prefix: string): string;
  /** 删临时目录（`recursive` + `force` 保证清理不抛错） */
  export function rmSync(path: string, options?: { recursive?: boolean; force?: boolean }): void;
}

declare module 'node:child_process' {
  /** 同步执行外部命令（AC-33 ②：`python -c "...ET.fromstring..."`，退出码非 0 即抛错） */
  export function execFileSync(
    file: string,
    args?: readonly string[],
    options?: { encoding?: 'utf8'; stdio?: 'pipe' | 'inherit' | 'ignore' },
  ): string;
}

declare module 'node:os' {
  /** 系统临时目录 */
  export function tmpdir(): string;
}

declare module 'node:path' {
  export function join(...parts: string[]): string;
  export function resolve(...parts: string[]): string;
  export function dirname(path: string): string;
}

declare module 'node:url' {
  /** `import.meta.url` → 文件系统路径（测试里不依赖 `process.cwd()`） */
  export function fileURLToPath(url: string | URL): string;
}
