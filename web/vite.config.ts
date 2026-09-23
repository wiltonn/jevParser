import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The design system is vendored once, in the Python package, and the HTML
// report inlines it from there.  The app imports the same files rather than a
// copy, so the two can never drift apart.
const design = fileURLToPath(new URL("../src/jev_diff/render/design", import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@design": design } },
  server: {
    port: 5173,
    fs: { allow: [".", design] },
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  build: { outDir: "dist", sourcemap: true },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
