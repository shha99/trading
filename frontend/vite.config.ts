import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 로컬 개발 시 프론트(5173)에서 /api 요청을 백엔드(8000)로 프록시.
      // 배포 환경에서는 VITE_API_BASE 환경변수로 실제 API 주소를 지정한다.
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
