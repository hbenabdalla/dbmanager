/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";
import { readFileSync } from "node:fs";
import { defineConfig } from "vite";

const pkg = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf-8")) as { version: string };

// vendor chunks by package-name prefix (Rolldown does not pull the dependencies of a listed
// package into its chunk the way the Rollup object form did, so the families are named here)
const VENDOR_CHUNKS: Record<string, string[]> = {
  react: ["react/", "react-dom/", "react-router", "scheduler/"],
  charts: ["recharts/", "d3-", "victory-vendor/", "internmap/", "decimal.js-light/", "react-smooth/", "recharts-scale/"],
  editor: ["@uiw/", "@codemirror/", "@lezer/", "codemirror/", "style-mod/", "w3c-keyname/", "crelt/"],
  data: ["@tanstack/"],
  ui: ["@radix-ui/", "@floating-ui/", "lucide-react/", "react-remove-scroll", "aria-hidden/"],
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // the frontend version shown on Platform -> About comes from package.json (same value as the backend)
  define: { __APP_VERSION__: JSON.stringify(pkg.version) },
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: process.env.VITE_API_PROXY ?? "http://127.0.0.1:8000", changeOrigin: false },
      "/healthz": "http://127.0.0.1:8000",
    },
  },
  build: {
    sourcemap: false,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        // function form: Vite 8 (Rolldown) no longer accepts the object form
        manualChunks(id: string) {
          if (!id.includes("node_modules")) return undefined;
          for (const [chunk, pkgs] of Object.entries(VENDOR_CHUNKS)) {
            if (pkgs.some((p) => id.includes(`/node_modules/${p}`))) return chunk;
          }
          return undefined;
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
