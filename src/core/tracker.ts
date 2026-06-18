import { eq, and, like, desc } from "drizzle-orm";
import { routes, routePhases, calibrationEvents } from "../db/schema.js";
import type { Db } from "../db/connection.js";
import { deriveOutcome, estimateCost, tokenize } from "./costs.js";
import type { CheckpointResult, Hazard } from "./ports.js";

export function checkpointPhase(
  db: Db,
  routeId: string,
  phaseLabel: string,
  actualCost: number,
  apiKey?: string,
): CheckpointResult {
  const route = db.select({ id: routes.id, totalExpected: routes.totalExpected })
    .from(routes)
    .where(eq(routes.id, routeId))
    .get();

  if (!route) throw new Error(`Route ${routeId} not found`);

  let phase = db.select()
    .from(routePhases)
    .where(and(eq(routePhases.routeId, routeId), eq(routePhases.label, phaseLabel), eq(routePhases.status, "pending")))
    .limit(1)
    .get();

  if (!phase) {
    const matches = db.select()
      .from(routePhases)
      .where(and(eq(routePhases.routeId, routeId), eq(routePhases.status, "pending")))
      .orderBy(routePhases.sequence)
      .limit(5)
      .all();

    for (const p of matches) {
      const prefix = p.label.split("(")[0].trim();
      if (phaseLabel.startsWith(prefix) || phaseLabel.includes(p.label)) {
        phase = p as typeof routePhases.$inferSelect;
        break;
      }
    }
  }

  if (!phase) throw new Error(`No pending phase matching "${phaseLabel}" in route ${routeId}`);

  const outcome = deriveOutcome(phase.expectedCost, actualCost);
  const deviationRatio = Math.round((actualCost / phase.expectedCost) * 100) / 100;

  db.update(routePhases)
    .set({ status: "done", actualCost, outcome })
    .where(eq(routePhases.id, phase.id))
    .run();

  const labelTokens = tokenize(phaseLabel);
  db.insert(calibrationEvents).values({
    action: phase.action,
    phaseLabelTokens: labelTokens,
    expectedCost: phase.expectedCost,
    actualCost,
    outcome,
    apiKey: apiKey ?? null,
    createdAt: new Date().toISOString(),
  }).run();

  const currentTotal = db.select({ total: routes.totalActual })
    .from(routes)
    .where(eq(routes.id, routeId))
    .get();

  const newTotal = (currentTotal?.total ?? 0) + actualCost;
  db.update(routes)
    .set({ totalActual: newTotal })
    .where(eq(routes.id, routeId))
    .run();

  const nextPhase = db.select({ label: routePhases.label, action: routePhases.action, expectedCost: routePhases.expectedCost })
    .from(routePhases)
    .where(and(eq(routePhases.routeId, routeId), eq(routePhases.status, "pending")))
    .orderBy(routePhases.sequence)
    .limit(1)
    .get();

  const remainingCount = db.$client.prepare("SELECT COUNT(*) as cnt FROM route_phases WHERE route_id = ? AND status != 'done'").get(routeId) as { cnt: number };
  const routeCompleted = remainingCount.cnt === 0;

  if (routeCompleted) {
    db.update(routes)
      .set({ status: "completed", completedAt: new Date().toISOString() })
      .where(eq(routes.id, routeId))
      .run();
  }

  const hazards = generateHazards(db, routeId, nextPhase?.label);

  return {
    phaseCompleted: phase.label,
    actualCost,
    expectedCost: phase.expectedCost,
    deviation: { ratio: deviationRatio, level: deviationRatio <= 1.3 ? "low" : deviationRatio <= 2.0 ? "medium" : "high", outcome },
    nextPhase: nextPhase ? { label: nextPhase.label, expectedCost: nextPhase.expectedCost, ci: [0, 0] } : null,
    hazards,
    routeCompleted,
  };
}

export function getRouteStatus(db: Db, routeId: string): Record<string, unknown> {
  const route = db.select().from(routes).where(eq(routes.id, routeId)).get();
  if (!route) return { error: "route not found" };

  const phases = db.select({
    label: routePhases.label,
    action: routePhases.action,
    expectedCost: routePhases.expectedCost,
    actualCost: routePhases.actualCost,
    outcome: routePhases.outcome,
    status: routePhases.status,
    sequence: routePhases.sequence,
  })
    .from(routePhases)
    .where(eq(routePhases.routeId, routeId))
    .orderBy(routePhases.sequence)
    .all();

  const doneCount = phases.filter(p => p.status === "done").length;

  return {
    routeId,
    project: route.project,
    status: route.status,
    phases,
    position: `${doneCount}/${phases.length}`,
    hazards: generateHazards(db, routeId, phases[doneCount]?.label),
  };
}

function generateHazards(db: Db, routeId: string, nextLabel?: string): Hazard[] {
  const hazards: Hazard[] = [];

  if (nextLabel) {
    const nextTokens = tokenize(nextLabel);
    if (nextTokens) {
      const tokens = nextTokens.split(" ");
      for (const token of tokens) {
        const row = db.$client.prepare(`
          SELECT COUNT(*) as cnt, AVG(actual_cost) as avg, MAX(actual_cost) as maxi, MIN(actual_cost) as mini
          FROM calibration_events
          WHERE phase_label_tokens LIKE ? AND action IS NOT NULL
          HAVING cnt >= 3
        `).get(`%${token}%`) as { cnt: number; avg: number | null; maxi: number | null; mini: number | null } | undefined;

        if (row?.avg && row?.maxi && row?.mini && row.maxi / Math.max(row.mini, 1) > 3) {
          hazards.push({
            type: "high_variance",
            detail: `${nextLabel} CI [${Math.round(row.mini)}-${Math.round(row.maxi)}] (${row.cnt} samples)`,
            suggestedBuffer: 1.5,
          });
          break;
        }
      }
    }
  }

  return hazards;
}
