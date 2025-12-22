// Local dev: run `npm run dev` here and `python backend/api.py` at repo root for /api proxy.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:5002"
    }
  }
});
