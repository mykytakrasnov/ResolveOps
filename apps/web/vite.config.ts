import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

import { localSyntheticApiPlugin } from "./dev/synthetic-api-plugin.ts";

const repositoryRoot = path.resolve(import.meta.dirname, "../..");
const agentApiUrl =
  process.env.RESOLVEOPS_AGENT_API_URL ?? "http://127.0.0.1:8000";
const fixtureRoot =
  process.env.RESOLVEOPS_SYNTHETIC_DATA_ROOT ??
  path.resolve(repositoryRoot, "data/generated");

export default defineConfig({
  plugins: [
    localSyntheticApiPlugin({
      fixtureRoot,
      hmacSecret:
        process.env.SYNTHETIC_API_HMAC_SECRET ??
        "resolveops-local-development-only",
    }),
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  server: {
    proxy: {
      "/api/v1/runs": {
        target: agentApiUrl,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
});
