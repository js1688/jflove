import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'path';

/**
 * 桌面端前端构建配置
 *
 * 与 Web 端（jflove-web/vite.config.ts）的关键差异：
 *   - `base: './'`：产物由 QtWebEngine 以 `file://` 加载，资源必须走相对路径，
 *     否则 `/assets/...` 会被解析成文件系统根目录而 404；
 *   - 无 dev server 代理：桌面端不走 HTTP 访问后端，所有请求经 QWebChannel
 *     交给 Python 侧既有的 `http_client.py`（安全宪法 §9.5.16）。
 *
 * `npm run dev` 仍可用：此时页面在浏览器里打开、没有 QWebChannel，
 * 桥调用会失败并给出明确提示（仅用于调样式，不做功能联调）。
 */
export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 3100,
    open: false,
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    // 本地加载不需要拆包优化，单文件更省心
    assetsInlineLimit: 4096,
  },
});
