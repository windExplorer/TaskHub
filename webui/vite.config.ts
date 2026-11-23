import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const BACKEND = process.env.BACKEND_URL ?? "http://127.0.0.1:9000";

// 逐前缀代理到本地后端（与生产同源直出保持一致的相对路径写法）；
// /ws/* 是 WebSocket，必须显式 ws: true，漏配会导致监控页实时数据全哑。
const API_PREFIXES = [
  "/health",
  "/monitor",
  "/device",
  "/self-check",
  "/stats",
  "/config",
  "/services",
  "/queue",
  "/tasks",
];

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // 生产由 FastAPI 挂在 /ui 下直出，资源路径必须带 /ui 前缀
  base: "/ui/",
  server: {
    port: 5174,
    proxy: {
      ...Object.fromEntries(
        API_PREFIXES.map((p) => [p, { target: BACKEND, changeOrigin: true }]),
      ),
      "/ws": { target: BACKEND, changeOrigin: true, ws: true },
    },
  },
  build: { target: "es2022", chunkSizeWarningLimit: 1200 },
});
