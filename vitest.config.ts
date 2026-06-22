import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["tests/**/*.test.ts"],
    coverage: {
      provider: "v8",
      include: ["src/**/*.ts"],
      exclude: ["src/cli.ts", "src/server.ts", "src/db/connection.ts"],
      thresholds: {
        branches: 60,
        functions: 55,
        lines: 55,
        statements: 55,
      },
    },
  },
});
