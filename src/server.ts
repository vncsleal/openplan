import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
  ListResourcesRequestSchema,
  ReadResourceRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { createStore } from "./db/store.js";
import { openDatabase, closeDatabase } from "./db/connection.js";
import { loadConfig, getDataDir } from "./config.js";
import { handlePlan } from "./handlers/plan-handler.js";
import { handleCheckpoint } from "./handlers/checkpoint-handler.js";
import { handleReview } from "./handlers/review-handler.js";
import { getRouteResource, getProfilesResource, getSyncStatusResource } from "./handlers/resources.js";
import { createMeshSync } from "./adapters/mesh.js";
import { createTimerCostProbe, createShellCostProbe } from "./adapters/cost-probe.js";
import type { MeshSync } from "./core/ports.js";
import type { StructuredError } from "./core/domain.js";
import { join } from "node:path";
import { existsSync, writeFileSync } from "node:fs";

export async function startServer(): Promise<void> {
  const config = loadConfig();
  const dbPath = join(getDataDir(), "openplan.db");
  const db = openDatabase(dbPath);
  const store = createStore(db, config.identityId);
  // Prevent dangling connection on unhandled rejections
  process.on("unhandledRejection", () => closeDatabase());

  // Host detection from env vars
  const hostId = process.env.OPENCODE_SESSION_ID
    ? ("opencode" as const)
    : process.env.CLAUDE_SESSION_ID
      ? ("claude" as const)
      : process.env.CURSOR_SESSION_ID
        ? ("cursor" as const)
        : ("unknown" as const);

  const meshSync: MeshSync = createMeshSync(config.meshUrl, config.apiKey);

  // Cost probe: use shell command if configured, otherwise timer-based
  const costProbe = config.costProbeCommand ? createShellCostProbe(config.costProbeCommand) : createTimerCostProbe();

  // Background sync: push unsynced checkpoints and pull baselines every 5 minutes
  const syncInterval = setInterval(
    async () => {
      try {
        const unsynced = store.getUnsyncedCalibrationEvents();
        if (unsynced.length > 0) {
          const ok = await meshSync.syncCheckpoints(unsynced);
          if (ok) store.markCalibrationSynced(unsynced.map((e) => e.id));
        }
        const baselines = await meshSync.fetchBaselines();
        if (baselines.length > 0) {
          store.setBaselines(baselines);
        }
      } catch {
        // Background sync failures are non-fatal; retry on next interval
      }
    },
    5 * 60 * 1000,
  );
  syncInterval.unref();

  // Ship at rest
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

  const server = new Server(
    {
      name: "openplan",
      version: "0.1.9",
    },
    {
      capabilities: {
        tools: {},
        resources: {},
      },
    },
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: [
      {
        name: "plan",
        description: "Decompose a goal into a costed route with phases, estimates, and evidence",
        inputSchema: {
          type: "object",
          properties: {
            goal: { type: "string", description: "The goal to plan" },
            context: { type: "string", description: "Optional context for better decomposition" },
            replan: { type: "boolean", description: "Archive current route and create fresh decomposition" },
            project: { type: "string", description: "Project name (defaults to OPENPLAN_PROJECT env or 'default')" },
          },
          required: ["goal"],
        },
        annotations: {
          readOnlyHint: true,
        },
      },
      {
        name: "checkpoint",
        description: "Record phase completion with cost, correct a cost, or get current route state",
        inputSchema: {
          type: "object",
          properties: {
            phase: { type: "string", description: "Phase label to checkpoint (omit for status check)" },
            actual_cost: { type: "number", description: "Actual cost in seconds for this phase" },
            correct: { type: "number", description: "Correct the last actual_cost for this phase" },
            route_id: { type: "string", description: "Route ID (optional if project is provided)" },
            project: { type: "string", description: "Project name (optional if route_id is provided)" },
          },
        },
        annotations: {
          destructiveHint: true,
        },
      },
      {
        name: "review",
        description: "Session retrospective with summary, deviations, accuracy, cost/path learning, and diagnostics",
        inputSchema: {
          type: "object",
          properties: {
            route_id: { type: "string", description: "Route ID (optional if project is provided)" },
            project: { type: "string", description: "Project name (optional if route_id is provided)" },
          },
        },
        annotations: {
          readOnlyHint: true,
        },
      },
    ],
  }));

  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const { name, arguments: args } = request.params;
    const safeArgs = args as Record<string, unknown> | undefined;
    const project = (safeArgs?.project as string | undefined) ?? process.env.OPENPLAN_PROJECT ?? "default";

    switch (name) {
      case "plan": {
        const goal = safeArgs?.goal;
        if (typeof goal !== "string" || goal.trim().length === 0) {
          return {
            content: [
              {
                type: "text",
                text: JSON.stringify({
                  error: { code: "INVALID_ARGUMENT", message: "goal is required and must be non-empty", param: "goal" },
                }),
              },
            ],
          };
        }
        const result = handlePlan({
          goal,
          context: typeof safeArgs?.context === "string" ? safeArgs.context : undefined,
          replan: typeof safeArgs?.replan === "boolean" ? safeArgs.replan : undefined,
          project,
          store,
        });

        if (!("error" in result)) {
          const anchorPath = join(config.projectRoot, ".openplan");
          try {
            writeFileSync(anchorPath, JSON.stringify({ project, routeId: result.id }, null, 2), "utf-8");
          } catch {
            // Non-fatal: anchor file is a convenience
          }
        }

        return { content: [{ type: "text", text: JSON.stringify(result) }] };
      }

      case "checkpoint": {
        const probeCost = costProbe.stop();

        const result = handleCheckpoint({
          phase: typeof safeArgs?.phase === "string" ? safeArgs.phase : undefined,
          actualCost: typeof safeArgs?.actual_cost === "number" ? safeArgs.actual_cost : (probeCost ?? undefined),
          correct: typeof safeArgs?.correct === "number" ? safeArgs.correct : undefined,
          routeId: typeof safeArgs?.route_id === "string" ? safeArgs.route_id : undefined,
          project: typeof safeArgs?.project === "string" ? safeArgs.project : undefined,
          store,
        });

        if (!("error" in result) && "nextPhase" in result && result.nextPhase) {
          costProbe.start();
        }

        return { content: [{ type: "text", text: JSON.stringify(result) }] };
      }

      case "review": {
        const meshReachable = await meshSync.isReachable();
        const result = handleReview({
          routeId: typeof safeArgs?.route_id === "string" ? safeArgs.route_id : undefined,
          project: typeof safeArgs?.project === "string" ? safeArgs.project : undefined,
          store,
          meshReachable,
        });
        return { content: [{ type: "text", text: JSON.stringify(result) }] };
      }

      default:
        return {
          content: [
            {
              type: "text",
              text: JSON.stringify({
                error: { code: "INVALID_ARGUMENT", message: `Unknown tool: ${name}` },
              } as StructuredError),
            },
          ],
        };
    }
  });

  server.setRequestHandler(ListResourcesRequestSchema, async () => {
    const meshReachable = await meshSync.isReachable();
    return {
      resources: [
        {
          uri: "openplan://{project}/route",
          name: "Current Route State",
          description: "Read current route state for a project: openplan://{project}/route",
          mimeType: "application/json",
        },
        {
          uri: "openplan://profiles",
          name: "Personal Profiles",
          description: "Personal bias, accuracy by action, sample counts",
          mimeType: "application/json",
        },
        {
          uri: "openplan://sync-status",
          name: "Mesh Sync Status",
          description: `Health check: mesh reachable (${meshReachable ? "yes" : "no"}), pending checkpoints, buffer, version`,
          mimeType: "application/json",
        },
      ],
    };
  });

  server.setRequestHandler(ReadResourceRequestSchema, async (request) => {
    const { uri } = request.params;

    if (uri === "openplan://profiles") {
      const resource = getProfilesResource(store);
      return { contents: [resource] };
    }

    if (uri === "openplan://sync-status") {
      const meshReachable = await meshSync.isReachable();
      const resource = getSyncStatusResource(store, meshReachable);
      return { contents: [resource] };
    }

    const routePattern = /^openplan:\/\/([^/]+)\/route$/;
    const routeMatch = uri.match(routePattern);
    if (routeMatch) {
      const resourceProject = routeMatch[1];
      const resource = getRouteResource(resourceProject, store);
      if (!resource) {
        return {
          contents: [
            {
              uri,
              mimeType: "application/json",
              text: JSON.stringify({
                error: { code: "NOT_FOUND", message: `No active route for project "${resourceProject}"` },
              }),
            },
          ],
        };
      }
      return { contents: [resource] };
    }

    return {
      contents: [
        {
          uri,
          mimeType: "application/json",
          text: JSON.stringify({ error: { code: "INVALID_ARGUMENT", message: `Unknown resource: ${uri}` } }),
        },
      ],
    };
  });

  const transport = new StdioServerTransport();
  await server.connect(transport);
}
