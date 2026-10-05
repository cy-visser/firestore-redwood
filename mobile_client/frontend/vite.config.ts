import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

declare const process: { env: Record<string, string | undefined> };

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      // Two apps, one project. The console shares the Tailwind theme, the
      // types and the SSE client with the phone, so making it a second HTML
      // entry point rather than a second project avoids a router dependency,
      // a second node_modules and a second dev server.
      input: {
        main: resolve(__dirname, "index.html"),
        console: resolve(__dirname, "console.html"),
      },
    },
  },
  server: {
    port: Number(process.env.VITE_PORT || 5174),
    host: "0.0.0.0",
    proxy: {
      "/api": {
        target: `http://localhost:${process.env.VITE_BACKEND_PORT || 8087}`,
        changeOrigin: true,
      },
    },
  },
});
