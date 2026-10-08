import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": process.env.VITE_API_PROXY ?? "http://127.0.0.1:8080",
      "/knowledge": process.env.VITE_API_PROXY ?? "http://127.0.0.1:8080",
      "/agent": process.env.VITE_API_PROXY ?? "http://127.0.0.1:8080",
    },
  },
});
