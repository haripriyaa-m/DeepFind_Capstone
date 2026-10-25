import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev mode: run the backend yourself (python backend/main.py --port 8765) and Vite
// proxies /api to it. In the desktop app the backend serves the built UI itself.
const apiTarget = process.env.DEEPFIND_API ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    proxy: { "/api": { target: apiTarget, changeOrigin: true } },
  },
  envPrefix: ["VITE_", "TAURI_ENV_"],
  build: {
    target: "es2021",
    sourcemap: false,
    chunkSizeWarningLimit: 800,
  },
});
