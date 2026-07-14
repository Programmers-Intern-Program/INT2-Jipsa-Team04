import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // 백엔드(8080)로 /api 요청을 그대로 넘겨준다 - CORS 설정 없이도 동작하고,
      // MyDocumentsView.tsx의 기존 /api/documents/... 상대경로 호출들과도 방식이 통일된다.
      "/api": {
        target: "http://localhost:8080",
        changeOrigin: true,
      },
    },
  },
})
