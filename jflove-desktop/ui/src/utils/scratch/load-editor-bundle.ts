/**
 * CM6 离线包加载器（**懒加载 + 幂等**）
 *
 * 包由 `jflove-desktop/scripts/build-codemirror-bundle.mjs` 在构建期产出到
 * `ui/public/vendor/editor.bundle.js`（单个 IIFE），副作用挂载 `window.__JFLOVE_CM__`
 * 与 `window.cmModules`（设计文档 v1.6.0 §6.1.1）。
 *
 * 三条硬约束：
 * 1. **懒加载**：`App.tsx` 启动**不**加载内核（首屏 < 300 ms 不受影响）；
 *    只有 `ScratchEditorHost` 首次真正要建实例时才 `await loadEditorBundle()`（§4.1.3）。
 * 2. **幂等**：并发/重复调用只注入一次 `<script>`，返回**同一个** Promise；
 *    即使模块被重复求值（HMR / 双实例），也靠 `data-jflove-editor-bundle` 标记复用同一个标签。
 * 3. **失败可重试**：加载失败时清空缓存的 Promise 并移除坏标签，下次调用重新注入；
 *    错误信息**只含资源名**，不含任何用户内容（安全宪法 §9.4 第 14 条）。
 *
 * 为什么用**相对路径**：页面由 QtWebEngine 以 `file://` 加载，绝对路径
 * `/vendor/editor.bundle.js` 会解析到**文件系统根**（`file:///vendor/...`）⇒ 必然失败。
 * 理由与写法照 `ui/src/utils/mermaid/loader.ts:11-16`（与 `vite.config.ts` 的 `base: './'` 一致）。
 */
import type { JFLoveCmApi } from '../../types/codemirror-bundle';
import versionJson from '../../../public/vendor/editor.version.json';

/**
 * 构建期清单（由 `scripts/build-codemirror-bundle.mjs` 生成）。
 * 用 JSON import 而非 fetch：Vite 构建期内联，运行期零请求；
 * 同时**缺文件时 `vite build` 直接失败**（设计文档 §6.1.1「消费方式」①）。
 */
const MANIFEST = versionJson as { codemirror: string; merge: string; sha256: string; bytes: number };

/** 构建期记录的内核版本（`@codemirror/view`）；运行期与产物内注入版本比对，只告警不阻断 */
export const EXPECTED_CODEMIRROR_VERSION = MANIFEST.codemirror;

/** 离线包 URL：**相对路径是硬约束**（见文件头） */
const BUNDLE_URL = './vendor/editor.bundle.js';

/** 注入标记：用于跨重复模块实例复用同一个 `<script>` */
const SCRIPT_MARKER = 'data-jflove-editor-bundle';

/** 单例加载 Promise：并发调用只注入一次 script（成功后长期复用） */
let loadPromise: Promise<JFLoveCmApi> | null = null;

/** 内核是否已挂载（不触发加载） */
function mounted(): JFLoveCmApi | null {
  const api = window.__JFLOVE_CM__;
  return api && typeof api.EditorView === 'function' ? api : null;
}

/** 内核是否已就绪（不同步触发加载）：供 UI 决定是否显示"正在加载编辑器"骨架 */
export function isEditorBundleLoaded(): boolean {
  return mounted() !== null;
}

/** 已注入的 `<script>`（未挂在全局上时可能是"正在加载"或"上次失败后残留"） */
function injectedScript(): HTMLScriptElement | null {
  return document.querySelector<HTMLScriptElement>(`script[${SCRIPT_MARKER}]`);
}

function createScript(): HTMLScriptElement {
  const script = document.createElement('script');
  script.src = BUNDLE_URL;
  script.async = true;
  // 本地静态资源，不需要 CORS/crossorigin
  script.setAttribute(SCRIPT_MARKER, '');
  document.head.appendChild(script);
  return script;
}

/** 等待 `<script>` 的 load / error；两种结局都解绑监听，避免重复回调 */
function waitForScript(script: HTMLScriptElement): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    function cleanup(): void {
      script.removeEventListener('load', onLoad);
      script.removeEventListener('error', onError);
    }
    function onLoad(): void {
      cleanup();
      resolve();
    }
    function onError(): void {
      cleanup();
      // 只报资源名，不带任何用户内容
      reject(new Error('编辑器内核资源加载失败（vendor/editor.bundle.js 缺失或损坏）'));
    }
    script.addEventListener('load', onLoad);
    script.addEventListener('error', onError);
  });
}

/** 版本一致性检查：不一致只告警（同 mermaid loader 的取舍：功能可用，但可能是构建事故） */
function warnOnVersionMismatch(api: JFLoveCmApi): void {
  const expectedMajorMinor = EXPECTED_CODEMIRROR_VERSION.split('.').slice(0, 2).join('.');
  const actual = api.version?.codemirror;
  if (typeof actual === 'string' && !actual.startsWith(expectedMajorMinor)) {
    console.warn(
      `[editor] 内核版本 ${actual} 与构建清单 ${EXPECTED_CODEMIRROR_VERSION} 不一致，` +
        '请重新执行 node jflove-desktop/scripts/build-codemirror-bundle.mjs 并重建前端',
    );
  }
}

/** 真正执行注入（调用方保证只在未挂载时进入） */
async function inject(): Promise<JFLoveCmApi> {
  const script = injectedScript() ?? createScript();
  try {
    await waitForScript(script);
  } catch (e) {
    // 移除坏标签：下次调用重新注入（允许重试）
    script.remove();
    throw e;
  }

  const api = mounted();
  if (!api) {
    script.remove();
    throw new Error('编辑器内核加载后未挂载 window.__JFLOVE_CM__');
  }
  warnOnVersionMismatch(api);
  return api;
}

/**
 * 加载并返回 CM6 内核（`window.__JFLOVE_CM__`）。
 *
 * - 已挂载 ⇒ 立即 resolve（不进网络、不加标签）；
 * - 正在加载 ⇒ 复用同一个 Promise（**并发只注入一次**）；
 * - 失败 ⇒ 清空缓存并允许下次重试（不永久降级）。
 */
export function loadEditorBundle(): Promise<JFLoveCmApi> {
  const ready = mounted();
  if (ready) return Promise.resolve(ready);
  if (loadPromise) return loadPromise;

  loadPromise = inject().catch((e: unknown) => {
    loadPromise = null; // 允许重试
    throw e;
  });
  return loadPromise;
}
