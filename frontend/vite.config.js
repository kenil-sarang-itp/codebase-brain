/**
 * Vite configuration.
 *
 * In development, `/api` requests are proxied to the backend so the frontend
 * and backend can run on separate ports without CORS friction. In production
 * the build is static and Nginx performs the same `/api` proxying.
 */
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      // Forward API calls to the backend container during development.
      "/api": {
        target: process.env.VITE_BACKEND_URL || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
