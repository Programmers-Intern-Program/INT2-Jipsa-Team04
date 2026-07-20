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
      //
      // docker-compose로 frontend까지 컨테이너로 띄우면 이 프록시가 실행되는 곳도
      // frontend 컨테이너 "안"이라 localhost가 backend 컨테이너를 가리키지 않는다
      // (컨테이너별로 네트워크 네임스페이스가 분리됨) - 그래서 VITE_API_TARGET 환경변수로
      // docker-compose.yml의 frontend 서비스에서만 http://backend:8080을 주입하고,
      // 로컬에서 npm run dev로 직접 돌릴 때는 기존처럼 localhost:8080로 폴백한다.
      "/api": {
        target: process.env.VITE_API_TARGET ?? "http://localhost:8080",
        changeOrigin: true,
      },
    },
  },
})
