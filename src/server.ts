import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { FastMCP } from "fastmcp";
import { z } from "zod";
import { createShellCostProbe, createTimerCostProbe } from "./adapters/cost-probe.js";
import { createMeshSync } from "./adapters/mesh.js";
import { getDataDir, loadConfig } from "./config.js";
import type { StructuredError } from "./core/domain.js";
import type { MeshSync } from "./core/ports.js";
import { closeDatabase, openDatabase } from "./db/connection.js";
import { createStore } from "./db/store.js";
import { handleCheckpoint } from "./handlers/checkpoint-handler.js";
import { handlePlan } from "./handlers/plan-handler.js";
import { getProfilesResource, getRouteResource, getSyncStatusResource } from "./handlers/resources.js";
import { handleReview } from "./handlers/review-handler.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const pkg = JSON.parse(readFileSync(join(__dirname, "..", "package.json"), "utf-8")) as { version: string };

export async function startServer(): Promise<void> {
  const config = loadConfig();
  const dbPath = join(getDataDir(), "openplan.db");
  const db = openDatabase(dbPath);
  const store = createStore(db, config.identityId);
  process.on("unhandledRejection", () => closeDatabase());

  const hostId = process.env.OPENCODE_SESSION_ID
    ? ("opencode" as const)
    : process.env.CLAUDE_SESSION_ID
      ? ("claude" as const)
      : process.env.CURSOR_SESSION_ID
        ? ("cursor" as const)
        : ("unknown" as const);

  const meshSync: MeshSync = createMeshSync(config.meshUrl, config.apiKey);

  const costProbe = config.costProbeCommand ? createShellCostProbe(config.costProbeCommand) : createTimerCostProbe();

  (async () => {
    try {
      const baselines = await meshSync.fetchBaselines();
      if (baselines !== null) store.setBaselines(baselines);
    } catch {
      // Non-fatal
    }
  })();

  const syncInterval = setInterval(
    async () => {
      try {
        const unsynced = store.getUnsyncedCalibrationEvents();
        if (unsynced.length > 0) {
          const ok = await meshSync.syncCheckpoints(unsynced);
          if (ok) store.markCalibrationSynced(unsynced.map((e) => e.id));
        }
        const baselines = await meshSync.fetchBaselines();
        if (baselines !== null) {
          store.setBaselines(baselines);
        }
      } catch {
        // Background sync failures are non-fatal
      }
    },
    5 * 60 * 1000,
  );
  syncInterval.unref();

  let isShuttingDown = false;

  function shutdown(): void {
    if (isShuttingDown) return;
    isShuttingDown = true;
    clearInterval(syncInterval);
    closeDatabase();
    process.exit(0);
  }

  process.on("SIGTERM", shutdown);
  process.on("SIGINT", shutdown);

  const server = new FastMCP({
    name: "openplan",
    version: pkg.version as `${number}.${number}.${number}`,
  });

  // ── plan tool ─────────────────────────────────────────────────────────────

  server.addTool({
    name: "plan",
    description: "Decompose a goal into a costed route. Returns phases with estimates, evidence (hazards), personal bias, and archived routes",
    parameters: z.object({
      goal: z.string().min(1, "goal is required"),
      context: z.string().optional(),
      replan: z.boolean().optional(),
      project: z.string().optional(),
    }),
    annotations: { readOnlyHint: true },
    execute: async (args) => {
      const project = args.project ?? process.env.OPENPLAN_PROJECT ?? "default";
      if (!args.goal || args.goal.trim().length === 0) {
        return JSON.stringify({
          error: { code: "INVALID_ARGUMENT", message: "goal is required and must be non-empty", param: "goal" },
        } as StructuredError);
      }

      let isPro = false;
      if (config.apiKey) {
        try {
          const acctResp = await fetch(`${config.meshUrl ?? "https://api.openplan.cc"}/v1/account`, {
            headers: { Authorization: `Bearer ${config.apiKey}` },
            signal: AbortSignal.timeout(3000),
          });
          if (acctResp.ok) {
            const acct = (await acctResp.json()) as Record<string, unknown>;
            isPro = acct.tier === "pro";
          }
        } catch {
          // assume Free
        }
      }

      const result = handlePlan({
        goal: args.goal.trim(),
        context: args.context,
        replan: args.replan,
        project,
        store,
        isPro,
      });

      if (!("error" in result)) {
        const anchorPath = join(config.projectRoot, ".openplan");
        try {
          writeFileSync(anchorPath, JSON.stringify({ project, routeId: result.id }, null, 2), "utf-8");
        } catch {
          // Non-fatal
        }
        costProbe.start();
      }

      return JSON.stringify(result);
    },
  });

  // ── checkpoint tool ───────────────────────────────────────────────────────

  server.addTool({
    name: "checkpoint",
    description: "Record phase completion with cost, correct a cost, or get current route state",
    parameters: z.object({
      phase: z.string().optional(),
      actual_cost: z.number().optional(),
      correct: z.number().optional(),
      route_id: z.string().optional(),
      project: z.string().optional(),
    }),
    annotations: { destructiveHint: true },
    execute: async (args) => {
      const project = args.project ?? process.env.OPENPLAN_PROJECT ?? "default";
      const probeCost = costProbe.stop();

      const result = handleCheckpoint({
        phase: args.phase,
        actualCost: args.actual_cost ?? probeCost ?? undefined,
        correct: args.correct,
        routeId: args.route_id,
        project,
        store,
      });

      if (!("error" in result) && "nextPhase" in result && result.nextPhase) {
        costProbe.start();
      }

      return JSON.stringify(result);
    },
  });

  // ── review tool ───────────────────────────────────────────────────────────

  server.addTool({
    name: "review",
    description: "Session retrospective with summary, deviations, accuracy, cost/path learning, diagnostics, and mesh sync status",
    parameters: z.object({
      route_id: z.string().optional(),
      project: z.string().optional(),
    }),
    annotations: { readOnlyHint: true },
    execute: async (args) => {
      const project = args.project ?? process.env.OPENPLAN_PROJECT ?? "default";
      const meshReachable = await meshSync.isReachable();
      const result = handleReview({ routeId: args.route_id, project, store, meshReachable });
      return JSON.stringify(result);
    },
  });

  // ── Resources ─────────────────────────────────────────────────────────────

  server.addResourceTemplate({
    uriTemplate: "openplan://{project}/route",
    name: "Current Route State",
    mimeType: "application/json",
    arguments: [{ name: "project", description: "Project name", required: true }],
    async load({ project }) {
      const resource = getRouteResource(project, store);
      if (!resource) {
        return {
          text: JSON.stringify({ error: { code: "NOT_FOUND", message: `No active route for project "${project}"` } }),
        };
      }
      return { text: resource.text };
    },
  });

  server.addResource({
    uri: "openplan://profiles",
    name: "Personal Profiles",
    mimeType: "application/json",
    description: "Personal bias, accuracy by action, sample counts",
    async load() {
      let isPro = false;
      if (config.apiKey) {
        try {
          const acctResp = await fetch(`${config.meshUrl ?? "https://api.openplan.cc"}/v1/account`, {
            headers: { Authorization: `Bearer ${config.apiKey}` },
            signal: AbortSignal.timeout(3000),
          });
          if (acctResp.ok) {
            const acct = (await acctResp.json()) as Record<string, unknown>;
            isPro = acct.tier === "pro";
          }
        } catch {
          // assume Free
        }
      }
      const resource = getProfilesResource(store, isPro);
      return { text: resource.text };
    },
  });

  server.addResource({
    uri: "openplan://sync-status",
    name: "Mesh Sync Status",
    mimeType: "application/json",
    description: "Health check: mesh reachable, pending checkpoints, buffer, version",
    async load() {
      const meshReachable = await meshSync.isReachable();
      const resource = getSyncStatusResource(store, meshReachable);
      return { text: resource.text };
    },
  });

  await server.start({ transportType: "stdio" });
}
