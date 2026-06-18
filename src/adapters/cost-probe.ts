import type { CostProbe } from "../core/ports.js";
import { execSync } from "node:child_process";
import type { ExecSyncOptionsWithStringEncoding } from "node:child_process";

export function createTimerCostProbe(): CostProbe {
  let startTime: number | null = null;
  let lastSnapshot: number | null = null;

  return {
    start(): void {
      startTime = Date.now();
      lastSnapshot = process.cpuUsage().user;
    },

    stop(): number | null {
      if (startTime === null) return null;

      const elapsed = Date.now() - startTime;
      startTime = null;
      lastSnapshot = null;

      if (elapsed < 100) return null;

      return Math.round(elapsed);
    },
  };
}

export function createShellCostProbe(command: string): CostProbe {
  let startTime: number | null = null;

  return {
    start(): void {
      startTime = Date.now();
    },

    stop(): number | null {
      if (startTime === null) return null;
      const elapsed = Date.now() - startTime;
      startTime = null;

      try {
        const opts: ExecSyncOptionsWithStringEncoding = {
          encoding: "utf-8",
          timeout: 5000,
          shell: "/bin/sh",
        };
        const output = execSync(command, opts).toString().trim();
        const parsed = Number(output);
        if (!Number.isFinite(parsed)) return Math.round(elapsed);
        return Math.round(parsed);
      } catch (e) {
        console.error(
          `[openplan] Cost probe shell command failed: ${e instanceof Error ? e.message : "unknown error"}`,
        );
        return Math.round(elapsed);
      }
    },
  };
}
