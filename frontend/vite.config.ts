import { defineConfig } from "vite";
import { resolve } from "path";

export default defineConfig({
  build: {
    outDir: resolve(__dirname, "../app/static/frontend"),
    emptyOutDir: true,
    rollupOptions: {
      input: resolve(__dirname, "index.html"),
    },
  },
  server: {
    proxy: {
      "/api": "http://localhost:8099",
      "/login": "http://localhost:8099",
      "/logout": "http://localhost:8099",
      "/healthz": "http://localhost:8099",
    },
  },
});
