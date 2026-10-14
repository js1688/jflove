import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

/**
 * 桌面端前端（`ui/`）单元测试配置 —— Phase 6（v1.6.0）
 *
 * ## 为什么与 `vite.config.ts` 分开
 *
 * 产物构建配置（`vite.config.ts`）要绝对稳定：`base: './'`、单文件产物、无 sourcemap
 * 都是 `file://` 加载与打包流程的既定要求。测试期配置（jsdom、setup 文件）只服务于断言，
 * 因此**独立一份**，避免测试期字段影响 `npm run build` 的产物。
 *
 * ## 关键约定
 *
 * - `environment: 'jsdom'`：AC-30 / AC-31 / AC-35 等需要真实 DOM；
 * - `globals: false`：用例显式 `import { describe, it, expect } from 'vitest'`，
 *   这样 `npm run build` 的 `tsc --noEmit`（`include: ["src"]`，测试文件也在内）
 *   不需要任何全局类型声明；
 * - DOM 清理由 `src/test/setup.ts` 的 `afterEach(cleanup)` 负责
 *   （Testing Library 的自动清理依赖全局 `afterEach`，此处刻意不开 globals）；
 * - `@` 别名与 `vite.config.ts` 保持一致；
 * - CSS 不参与断言（`css: false`），避免无谓的样式解析。
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    environment: 'jsdom',
    globals: false,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    restoreMocks: true,
    clearMocks: true,
    css: false,
  },
});
