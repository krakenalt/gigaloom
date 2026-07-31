import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import type { Plugin } from "vite";
import { configDefaults, defineConfig } from "vitest/config";

const outputDirectory = process.env.GIGALOOM_WEB_OUTPUT ?? fileURLToPath(
  new URL("../src/gigaloom/ui/web/assets", import.meta.url),
);

function katexWoff2Only(): Plugin {
  return {
    enforce: "pre",
    name: "katex-woff2-only",
    transform(code, id) {
      if (!id.endsWith("/katex/dist/katex.min.css")) return null;
      return code.replace(
        /src:url\(([^)]+\.woff2)\) format\("woff2"\),url\([^)]+\.woff\) format\("woff"\),url\([^)]+\.ttf\) format\("truetype"\)/g,
        'src:url($1) format("woff2")',
      );
    },
  };
}

export default defineConfig({
  base: "/web/assets/",
  build: {
    assetsDir: "assets",
    assetsInlineLimit: 0,
    emptyOutDir: true,
    manifest: true,
    outDir: outputDirectory,
    rolldownOptions: {
      output: {
        assetFileNames: "assets/[name]-[hash][extname]",
        chunkFileNames: "assets/[name]-[hash].js",
        codeSplitting: {
          groups: [
            {
              includeDependenciesRecursively: false,
              name: "router",
              test: /node_modules[\\/]@tanstack[\\/](?:history|react-router|react-store|router-core|store)[\\/]/,
            },
          ],
        },
        entryFileNames: "assets/[name]-[hash].js",
      },
    },
    sourcemap: false,
    target: "es2022",
  },
  plugins: [katexWoff2Only(), react()],
  test: {
    environment: "node",
    exclude: [...configDefaults.exclude, "e2e/**"],
  },
});
