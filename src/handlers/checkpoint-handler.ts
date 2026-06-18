import { eq, and, desc } from "drizzle-orm";
import { routes } from "../db/schema.js";
import type { Db } from "../db/connection.js";
import { checkpointPhase, getRouteStatus } from "../core/tracker.js";

export async function handleCheckpoint(
  db: Db,
  args: {
    phase?: string | null;
    actualCost?: number | null;
    routeId?: string | null;
    project?: string | null;
    apiKey?: string;
  },
): Promise<string> {
  const { phase, actualCost, routeId, project, apiKey } = args;

  if (!phase && !actualCost) {
    if (routeId) {
      return JSON.stringify(getRouteStatus(db, routeId));
    }
    const active = db.select({ id: routes.id, project: routes.project })
      .from(routes)
      .where(and(eq(routes.archived, false), eq(routes.status, "active")))
      .orderBy(desc(routes.createdAt))
      .limit(1)
      .get();
    if (!active) return JSON.stringify({ error: true, message: "no active route" });
    return JSON.stringify(getRouteStatus(db, active.id));
  }

  if (!phase || actualCost === undefined || actualCost === null) {
    return JSON.stringify({ error: true, message: "phase and actual_cost are required" });
  }

  let resolvedRouteId = routeId;
  if (!resolvedRouteId) {
    const active = db.select({ id: routes.id })
      .from(routes)
      .where(and(eq(routes.archived, false), eq(routes.status, "active")))
      .orderBy(desc(routes.createdAt))
      .limit(1)
      .get();
    if (!active) return JSON.stringify({ error: true, message: "no active route — call plan() first" });
    resolvedRouteId = active.id;
  }

  try {
    const result = checkpointPhase(db, resolvedRouteId, phase, actualCost, apiKey);
    return JSON.stringify(result, null, 2);
  } catch (err) {
    return JSON.stringify({ error: true, message: (err as Error).message });
  }
}
