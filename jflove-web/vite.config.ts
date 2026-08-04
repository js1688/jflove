import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'path';

/**
 * dev 环境 Service Worker 托管插件
 *
 * 目标：让 SW 在根路径 /sw.js 提供服务（scope 覆盖全站，可拦截 /jflove-stream/*）。
 * 生产构建输出 dist/sw.js；dev 下 Vite 不直接托管该文件，故在此中间件中
 * 调用 transformRequest 对 src/sw/index.ts 做转换，并附加 Service-Worker-Allowed 头
 * （允许 scope 扩到根路径，否则浏览器会因 scope 超出脚本目录而拒绝接管）。
 */
function swDevPlugin(): Plugin {
  return {
    name: 'jflove-sw-dev',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        if (req.url === '/sw.js' || (req.url && req.url.startsWith('/sw.js?'))) {
          try {
            const result = await server.transformRequest('/src/sw/index.ts');
            if (result) {
              res.statusCode = 200;
              res.setHeader('Content-Type', 'text/javascript');
              res.setHeader('Service-Worker-Allowed', '/');
              res.setHeader('Cache-Control', 'no-cache');
              res.end(result.code);
              return;
            }
          } catch {
            /* 转换失败则走默认处理（404） */
          }
        }
        next();
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), swDevPlugin()],
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
    rollupOptions: {
      // 双入口：应用（index.html）+ Service Worker 流式代理（sw.js）
      input: {
        main: path.resolve(__dirname, 'index.html'),
        sw: path.resolve(__dirname, 'src/sw/index.ts'),
      },
      output: {
        // SW 输出到根目录 sw.js（无 hash，注册路径固定）；其余资源带 hash 进 assets/
        entryFileNames: (chunk) => {
          if (chunk.name === 'sw') return 'sw.js';
          return 'assets/[name]-[hash].js';
        },
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash].[ext]',
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './tests/setup.ts',
  },
});
