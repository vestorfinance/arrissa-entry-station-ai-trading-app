import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Proxy API + WebSocket calls to the FastAPI backend (backend/main.py on :8000).
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'http://localhost:8000', ws: true },
    },
  },
})
