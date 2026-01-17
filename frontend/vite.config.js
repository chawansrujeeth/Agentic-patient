// Local dev: run `npm run dev` here and `python3 backend/api.py` at repo root for /api proxy.
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const backendTarget =
    env.VITE_API_PROXY_TARGET ||
    env.VITE_BACKEND_URL ||
    `http://localhost:${env.VITE_BACKEND_PORT || "5000"}`;

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": backendTarget,
      },
    },
  };
});
