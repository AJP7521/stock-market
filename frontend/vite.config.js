import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// The dev server forwards every /api request (HTTP and, later, WebSocket) to FastAPI.
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true, ws: true },
    },
  },
})
