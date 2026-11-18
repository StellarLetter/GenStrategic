import { defineConfig } from 'vite';

/**
 * Vite 配置。
 * 开发期把 /ws 与 /api 代理到后端（默认 8000），生产期由后端静态托管 dist/。
 * 见 docs/ARCHITECTURE.md §5。
 */
export default defineConfig({
  root: '.',
  publicDir: 'public',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    target: 'es2022',
    rollupOptions: {
      input: {
        main: 'index.html',
        waiting: 'waiting.html',
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
});
