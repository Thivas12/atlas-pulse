import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import securityHeaders from "./security-headers.json" with { type: "json" };

export default defineConfig({
  plugins: [react()],
  build: {
    // MapLibre's WebGL engine is an intentional lazy chunk; keep the initial app bundle small.
    chunkSizeWarningLimit: 1_100,
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  preview: {
    headers: securityHeaders,
  },
  test: {
    include: ["src/**/*.test.{ts,tsx}"],
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/main.tsx",
        "src/components/EventMap.tsx",
        "src/**/*.test.{ts,tsx}",
        "src/test/**",
      ],
      reporter: ["text", "json-summary"],
      thresholds: {
        lines: 80,
        functions: 80,
        statements: 80,
        branches: 75,
      },
    },
  },
});
