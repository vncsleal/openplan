import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { FastMCP } from "fastmcp";
import { z } from "zod";
import {
  createClaudeCostProbe,
  createCursorCostProbe,
  createNullCostProbe,
  createOpenCodeCostProbe,
  createShellCostProbe,
  isOpenCodeAvailable,
} from "./adapters/cost-probe.js";
import { createMeshSync } from "./adapters/mesh.js";
import { DEFAULT_MESH_URL, getDataDir, loadConfig } from "./config.js";
import type { StructuredError } from "./core/domain.js";
import { createLogger } from "./core/logger.js";
import type { MeshSync } from "./core/ports.js";
import { AccountResponse } from "./core/schemas.js";
import { closeDatabase, openDatabase } from "./db/connection.js";
import { createStore } from "./db/store.js";
import { handleCheckpoint } from "./handlers/checkpoint-handler.js";
import { handlePlan } from "./handlers/plan-handler.js";
import { getProfilesResource, getRouteResource, getSyncStatusResource } from "./handlers/resources.js";
import { handleReview } from "./handlers/review-handler.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const pkg = JSON.parse(readFileSync(join(__dirname, "..", "package.json"), "utf-8")) as { version: string };
const log = createLogger("server");

async function checkProTier(meshUrl: string, apiKey: string | null): Promise<boolean> {
  if (!apiKey) return false;
  try {
    const acctResp = await fetch(`${meshUrl}/v1/account`, {
      headers: { Authorization: `Bearer ${apiKey}` },
      signal: AbortSignal.timeout(3000),
    });
    if (acctResp.ok) {
      const body = await acctResp.json();
      const parsed = AccountResponse.parse(body);
      return parsed.tier === "pro";
    }
  } catch {
    log.debug("Account check failed, assuming Free");
  }
  return false;
}

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

  const costProbe = config.costProbeCommand
    ? createShellCostProbe(config.costProbeCommand)
    : hostId === "opencode" || isOpenCodeAvailable()
      ? createOpenCodeCostProbe()
      : hostId === "claude"
        ? createClaudeCostProbe()
        : hostId === "cursor"
          ? createCursorCostProbe()
          : createNullCostProbe();

  const meshUrl = config.meshUrl ?? DEFAULT_MESH_URL;

  (async () => {
    try {
      const baselines = await meshSync.fetchBaselines();
      if (baselines !== null) store.setBaselines(baselines);
    } catch {
      log.debug("Initial baseline fetch failed");
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
        log.warn("Background sync failed");
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
    description:
      "Decompose a goal into a costed route. Returns phases with estimates, confidence intervals (requires Mesh baselines, otherwise null), evidence (hazards), personal bias (requires Pro tier, otherwise null), and archived routes",
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

      const isPro = await checkProTier(meshUrl, config.apiKey);

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
          log.debug("Could not write .openplan anchor");
        }
        costProbe.start();
      }

      return JSON.stringify(result);
    },
  });

  // ── checkpoint tool ───────────────────────────────────────────────────────

  server.addTool({
    name: "checkpoint",
    description:
      "Record phase completion with cost, correct a cost, or get current route state. Costs accumulate across calls for the same phase (not idempotent)",
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
    description:
      "Session retrospective with summary, deviations, accuracy, cost/path learning, diagnostics, and mesh sync status",
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
      const isPro = await checkProTier(meshUrl, config.apiKey);
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
