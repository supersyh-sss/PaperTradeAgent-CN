import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  optimizeDeps: {
    // echarts-for-react 为 CJS 产物，强制预构建确保 dev 下 default 互操作一致
    include: ['echarts-for-react/lib/core'],
  },
  build: {
    // StockDetail（echarts）为按需懒加载 chunk，不阻塞首屏，放宽阈值避免误报
    chunkSizeWarningLimit: 700,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq, req) => {
            if (req.url?.includes('/stream')) {
              proxyReq.setHeader('Connection', 'keep-alive');
              proxyReq.setHeader('Cache-Control', 'no-cache');
            }
          });
          proxy.on('proxyRes', (proxyRes, req) => {
            if (req.url?.includes('/stream')) {
              delete proxyRes.headers['content-length'];
            }
          });
        }
      }
    }
  }
})
