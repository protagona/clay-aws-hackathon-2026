import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "../src/foundry/ui/web_dist",
    emptyOutDir: true,
    assetsDir: "assets",
    // Fonts are always files: font-src 'self' blocks data: URIs (design ADR-5).
    assetsInlineLimit: (file: string) => (/\.(woff2?|ttf|otf)$/.test(file) ? false : undefined),
  },
  server: { proxy: { "/api": { target: "http://127.0.0.1:8765", changeOrigin: false } } },
  test: { environment: "jsdom" },
});
