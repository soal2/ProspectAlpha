// @lovable.dev/vite-tanstack-config already includes the following — do NOT add them manually
// or the app will break with duplicate plugins:
//   - tanstackStart, viteReact, tailwindcss, tsConfigPaths, nitro (build-only using cloudflare as a default target),
//     componentTagger (dev-only), VITE_* env injection, @ path alias, React/TanStack dedupe,
//     error logger plugins, and sandbox detection (port/host/strictPort).
// You can pass additional config via defineConfig({ vite: { ... }, etc... }) if needed.
import { defineConfig } from "@lovable.dev/vite-tanstack-config";

export default defineConfig({
  tanstackStart: {
    server: { entry: "server" },
  },
  // The Lovable preset targets Cloudflare by default. Allow an explicit Node build
  // for local acceptance testing without changing the deployment target.
  nitro:
    process.env.GITHUB_PAGES_DEPLOY === "true" || process.env.LOCAL_NODE_PREVIEW === "true"
      ? { preset: "node-server" }
      : undefined,
  vite: {
    base: process.env.GITHUB_PAGES_DEPLOY === "true" ? "/ProspectAlpha/" : "/",
    resolve: { dedupe: ["react", "react-dom"] },
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target: process.env.VITE_DEV_API_PROXY ?? "http://127.0.0.1:8000",
          changeOrigin: true,
          rewrite: (path: string) => path.replace(/^\/api/, ""),
        },
      },
    },
    preview: { port: 5173 },
  },
});
