import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    env: {
      NEXT_PUBLIC_API_BASE_URL: "http://api.test.local",
      NEXT_PUBLIC_WS_BASE_URL: "ws://api.test.local",
      NEXT_PUBLIC_APP_URL: "http://app.test.local",
    },
  },
});
