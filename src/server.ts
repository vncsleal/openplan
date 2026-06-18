import { FastMCP } from "fastmcp";
import { z } from "zod";
import type Database from "better-sqlite3";
import { getConnection } from "./db/connection.js";
import { MeshAdapter } from "./adapters/mesh.js";
import { handlePlan } from "./handlers/plan-handler.js";
import { handleCheckpoint } from "./handlers/checkpoint-handler.js";
import { handleReview } from "./handlers/review-handler.js";
import { loadConfig, ensureConfig } from "./config.js";

export function createServer(): FastMCP {
  ensureConfig();
  const config = loadConfig();
  const conn = getConnection(config.core.dbPath);
  const mesh = new MeshAdapter(config.mesh.apiUrl, config.mesh.apiKey);

  const syncInterval = setInterval(async () => {
    try {
      await mesh.syncPending(conn);
    } catch {
      // retry next cycle
    }
  }, 300_000);

  const server = new FastMCP({
    name: "OpenPlan",
    version: "0.1.0",
    instructions: "Plan projects with plan(), track progress with checkpoint(), review results with review().",
  });

  server.addTool({
    name: "plan",
    description: "Decompose a goal into a costed route with phases, estimates, and evidence.",
    parameters: z.object({
      goal: z.string().min(1, "goal is required"),
      context: z.string().optional().default(""),
      replan: z.boolean().optional().default(false),
    }),
    execute: async (args, { log }) => {
      log.info("plan called");
      const result = await handlePlan(conn, args);
      return { content: [{ type: "text" as const, text: result }] };
    },
  });

  server.addTool({
    name: "checkpoint",
    description: "Record phase completion with cost, or get current route state (no args).",
    parameters: z.object({
      phase: z.string().optional(),
      actualCost: z.number().optional(),
      routeId: z.string().optional(),
      project: z.string().optional(),
    }),
    execute: async (args, { log }) => {
      log.info("checkpoint called");
      const result = await handleCheckpoint(conn, {
        phase: args.phase ?? null,
        actualCost: args.actualCost ?? null,
        routeId: args.routeId ?? null,
        project: args.project ?? null,
        apiKey: config.mesh.apiKey || undefined,
      });
      return { content: [{ type: "text" as const, text: result }] };
    },
  });

  server.addTool({
    name: "review",
    description: "Session retrospective — summary, deviations, learnings, self-diagnostics.",
    parameters: z.object({
      routeId: z.string().optional(),
      project: z.string().optional(),
    }),
    execute: async (args, { log }) => {
      log.info("review called");
      const result = await handleReview(conn, args);
      return { content: [{ type: "text" as const, text: result }] };
    },
  });

  server.addResource({
    uri: "openplan://profiles",
    name: "Profile",
    description: "Personal bias and accuracy stats",
    mimeType: "application/json",
    load: async () => {
      const { computePersonalBias } = await import("./core/costs.js");
      const bias = computePersonalBias(conn, config.mesh.apiKey || undefined);
      const totalCheckpoints = conn.prepare("SELECT COUNT(*) as cnt FROM calibration_events").get() as { cnt: number };
      return { text: JSON.stringify({ personalBias: bias, totalCheckpoints: totalCheckpoints.cnt }) };
    },
  });

  server.addResource({
    uri: "openplan://sync-status",
    name: "Sync Status",
    description: "Mesh sync health",
    mimeType: "application/json",
    load: async () => {
      const pending = conn.prepare("SELECT COUNT(*) as cnt FROM calibration_events WHERE synced = 0").get() as { cnt: number };
      return { text: JSON.stringify({ pendingCheckpoints: pending.cnt }) };
    },
  });

  // Cleanup on disconnect
  server.on("disconnect", () => {
    clearInterval(syncInterval);
  });

  return server;
}
