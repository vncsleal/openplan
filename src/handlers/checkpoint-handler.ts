import type Database from "better-sqlite3";
import { checkpointPhase, getRouteStatus } from "../core/tracker.js";

export async function handleCheckpoint(
  sqlite: Database.Database,
  args: {
    phase?: string | null;
    actualCost?: number | null;
    routeId?: string | null;
    project?: string | null;
    apiKey?: string;
  },
): Promise<string> {
  const { phase, actualCost, routeId, project, apiKey } = args;

  // Status mode
  if (!phase && !actualCost) {
    if (routeId) {
      return JSON.stringify(getRouteStatus(sqlite, routeId));
    }
    const active = sqlite.prepare(
      "SELECT id, project FROM routes WHERE archived = 0 AND status = 'active' ORDER BY created_at DESC LIMIT 1"
    ).get() as { id: string } | undefined;
    if (!active) return JSON.stringify({ error: true, message: "no active route" });
    return JSON.stringify(getRouteStatus(sqlite, active.id));
  }

  if (!phase || actualCost === undefined || actualCost === null) {
    return JSON.stringify({ error: true, message: "phase and actual_cost are required" });
  }

  let resolvedRouteId = routeId;
  if (!resolvedRouteId) {
    const active = sqlite.prepare(
      "SELECT id FROM routes WHERE archived = 0 AND status = 'active' ORDER BY created_at DESC LIMIT 1"
    ).get() as { id: string } | undefined;
    if (!active) return JSON.stringify({ error: true, message: "no active route — call plan() first" });
    resolvedRouteId = active.id;
  }

  try {
    const result = checkpointPhase(sqlite, resolvedRouteId, phase, actualCost, apiKey);
    return JSON.stringify(result, null, 2);
  } catch (err) {
    return JSON.stringify({ error: true, message: (err as Error).message });
  }
}
