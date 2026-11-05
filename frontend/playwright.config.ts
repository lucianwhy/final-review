import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  use: { baseURL: "http://127.0.0.1:5174", channel: "chrome" },
  webServer: [
    { command: "uv run python tests/workbench_server.py", cwd: "..", url: "http://127.0.0.1:8081/health", reuseExistingServer: false, timeout: 30_000 },
    { command: "npm run dev -- --host 127.0.0.1 --port 5174 --strictPort", url: "http://127.0.0.1:5174", env: { VITE_API_PROXY: "http://127.0.0.1:8081" }, reuseExistingServer: false, timeout: 30_000 },
  ],
});
