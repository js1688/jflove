import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'path';

// v1.4.0：移除 Service Worker 流式代理（Web 端边下边播统一走 MSE 主路径，
// 不依赖安全上下文，HTTP/HTTPS 均可用），删除 sw 插件与 sw 双入口。
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 3000,
    open: false,
    // 代理 API 请求到后端，避免开发时 CORS 问题
    proxy: {
      '/api': {
        target: 'http://localhost:8989',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:8989',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false, // 生产构建不泄露源代码路径
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './tests/setup.ts',
  },
});
