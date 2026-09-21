/**
 * Mermaid 离线包加载器
 *
 * 包由 `scripts/build_mermaid_bundle.mjs` 在构建期产出到 `public/vendor/mermaid.bundle.js`，
 * 挂载 `window.mermaid`。**不走 npm 依赖**，原因见设计文档 §4.2：
 * 三端（Web / 桌面端 / 移动端）必须使用同一份产物，才能保证同一份笔记渲染一致（AC-9）。
 */
import type { MermaidApi } from '../../types/mermaid';
import pkgManifest from '../../../public/vendor/mermaid.version.json';

// 桌面端改动：bundle 改用**相对路径**。
// 原实现 `/vendor/mermaid.bundle.js` 是绝对路径 —— 页面由 QtWebEngine 以 `file://`
// 加载时，绝对路径会解析到**文件系统根**（`file:///vendor/...`）→ 必然加载失败。
// 相对路径与 `vite.config.ts` 的 `base: './'` 一致，
// 最终落到 `file:///.../ui/dist/vendor/mermaid.bundle.js`。
const BUNDLE_URL = './vendor/mermaid.bundle.js';

/**
 * 构建期清单（由 `scripts/build_mermaid_bundle.mjs` 生成）。
 * 用 JSON import 而非 fetch：Vite 会在构建期把它内联，运行时零请求。
 */
const MANIFEST = pkgManifest as { mermaidVersion: string; sha256: string };

/** 渲染侧期望的引擎版本；与实际加载到的版本不一致时告警（三端一致性前提） */
export const EXPECTED_MERMAID_VERSION = MANIFEST.mermaidVersion;

/** 单例加载 Promise：并发调用只注入一次 script */
let loadPromise: Promise<MermaidApi> | null = null;

/**
 * 加载并返回 mermaid 实例。
 *
 * - 首次调用注入 `<script>`；后续调用复用同一个 Promise；
 * - 加载失败时清除 Promise，允许下次重试（避免永久降级）；
 * - 加载成功后校验版本与构建期清单一致：不一致只告警不阻断
 *   （图表仍能渲染，但同一份笔记可能与他端画得不同，属于配置事故）。
 */
export function loadMermaid(): Promise<MermaidApi> {
  if (window.mermaid) return Promise.resolve(window.mermaid);
  if (loadPromise) return loadPromise;

  loadPromise = new Promise<MermaidApi>((resolve, reject) => {
    const script = document.createElement('script');
    script.src = BUNDLE_URL;
    script.async = true;
    // 本地静态资源，不需要 CORS/crossorigin
    script.onload = () => {
      const m = window.mermaid;
      if (!m) {
        loadPromise = null;
        reject(new Error('渲染引擎加载后未挂载 window.mermaid'));
        return;
      }
      // 版本一致性检查（需求 AC-9：三端必须同版本）
      if (m.version && !m.version.startsWith(MANIFEST.mermaidVersion.split('.').slice(0, 2).join('.'))) {
        console.warn(
          `[mermaid] 引擎版本 ${m.version} 与构建清单 ${MANIFEST.mermaidVersion} 不一致，` +
            '图表可能与其他端渲染不一致；请重新执行 scripts/build_mermaid_bundle.mjs',
        );
      }
      resolve(m);
    };
    script.onerror = () => {
      script.remove();
      loadPromise = null; // 允许重试
      reject(new Error('渲染引擎资源加载失败'));
    };
    document.head.appendChild(script);
  });

  return loadPromise;
}

/** 引擎是否已就绪（不同步触发加载） */
export function isMermaidLoaded(): boolean {
  return Boolean(window.mermaid);
}
