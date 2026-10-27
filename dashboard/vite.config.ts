import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build straight into the Python package so `lucid serve` can host it.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../src/lucid/web", emptyOutDir: true, sourcemap: false },
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
});
