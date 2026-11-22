import { defineConfig } from 'vitest/config';

/**
 * 测试配置（与 `vite.config.ts` 分开，原因见那里的注释）。
 *
 * 官网的测试重点不是 UI 快照，而是**内容契约**：
 *   - 数据层能否正确读出 manifest、统计是否自洽
 *   - 页面渲染时是否真的用了数据（而不是硬编码）
 * 因此用 jsdom 做轻量渲染断言即可，不需要浏览器。
 */
export default defineConfig({
  test: {
    // 测试只校验数据层契约（不渲染 DOM），node 环境即可，无需 jsdom
    environment: 'node',
    include: ['tests/**/*.test.{ts,tsx}'],
    globals: true,
  },
});
