import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

/**
 * 官网构建配置。
 *
 * 内容数据（`content/*.json`）在**构建期**被 import，因此它们的变化会直接进入产物 ——
 * 不需要运行时再去请求，也就不会有"线上还是旧内容"。
 *
 * `base: '/'`：官网部署在站点根路径（由 nginx 直接 serve dist）。
 * 若要部署到子路径，改这里并把 nginx 的 location 一并调整。
 *
 * 测试配置单独放在 `vitest.config.ts`：vitest 自带一份嵌套的 vite，
 * 若在同一文件里同时配置构建与测试，插件类型会因两份 vite 而冲突。
 */
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: '/',
  server: { port: 4173 },
  preview: { port: 4173 },
});
