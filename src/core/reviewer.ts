import { eq, and, like, desc, sql } from "drizzle-orm";
import { routes, routePhases, calibrationEvents, completedSequences } from "../db/schema.js";
import type { Db } from "../db/connection.js";
import type { ReviewResult } from "./ports.js";

export function reviewRoute(
  db: Db,
  routeId?: string,
  project?: string,
): ReviewResult | { error: string } {
  let route: typeof routes.$inferSelect | undefined;

  if (routeId) {
    route = db.select().from(routes).where(eq(routes.id, routeId)).get();
  } else if (project) {
    route = db.select().from(routes)
      .where(and(eq(routes.project, project), eq(routes.archived, false)))
      .orderBy(desc(routes.createdAt))
      .limit(1)
      .get();
  } else {
    route = db.select().from(routes)
      .where(eq(routes.archived, false))
      .orderBy(desc(routes.createdAt))
      .limit(1)
      .get();
  }

  if (!route) return { error: "no route found" };

  const phases = db.select()
    .from(routePhases)
    .where(and(eq(routePhases.routeId, route.id), eq(routePhases.status, "done")))
    .orderBy(routePhases.sequence)
    .all();

  if (phases.length === 0) return { error: "no completed phases" };

  const totalExpected = phases.reduce((s, p) => s + p.expectedCost, 0);
  const totalActual = phases.reduce((s, p) => s + (p.actualCost ?? 0), 0);

  const accuracy = (totalExpected > 0 && totalActual > 0)
    ? Math.round(Math.min(totalActual / totalExpected, totalExpected / totalActual) * 100) / 100
    : 0;

  const deviations = phases.map(p => ({
    phase: p.label,
    expected: p.expectedCost,
    actual: p.actualCost ?? 0,
    ratio: p.expectedCost > 0 ? Math.round(((p.actualCost ?? 0) / p.expectedCost) * 100) / 100 : 0,
  }));

  const actionMap = new Map<string, { sum: number; count: number }>();
  for (const p of phases) {
    const entry = actionMap.get(p.action) ?? { sum: 0, count: 0 };
    if (p.actualCost && p.expectedCost > 0) {
      entry.sum += p.actualCost / p.expectedCost;
      entry.count += 1;
    }
    actionMap.set(p.action, entry);
  }

  const accuracyByAction: Record<string, { count: number; avgDeviation: number }> = {};
  for (const [action, val] of actionMap) {
    accuracyByAction[action] = {
      count: val.count,
      avgDeviation: val.count > 0 ? Math.round((val.sum / val.count) * 100) / 100 : 0,
    };
  }

  const costLearningRows = db.$client.prepare(`
    SELECT action, AVG(actual_cost) as avg_cost, COUNT(*) as samples
    FROM calibration_events
    GROUP BY action
    ORDER BY samples DESC
    LIMIT 5
  `).all() as Array<{ action: string; avg_cost: number; samples: number }>;

  const costLearning = costLearningRows.map(r => ({ action: r.action, avgCost: Math.round(r.avg_cost), samples: r.samples }));

  const actionSeq = phases.map(p => p.action).join(",");
  const efficiency = totalExpected > 0 ? totalActual / totalExpected : 1;

  const existingSeq = db.select({ cnt: sql<number>`COUNT(*)` })
    .from(completedSequences)
    .where(and(
      eq(completedSequences.goalTokens, route.goalTokens ?? ""),
      eq(completedSequences.actionSequence, actionSeq),
    ))
    .get();

  if (!existingSeq || existingSeq.cnt === 0) {
    db.insert(completedSequences).values({
      goalTokens: route.goalTokens ?? "",
      contextTokens: route.contextTokens ?? "",
      actionSequence: actionSeq,
      totalExpected,
      totalActual,
      efficiency,
      outcome: "success",
      createdAt: new Date().toISOString(),
    }).run();
  }

  const tokens = (route.goalTokens ?? "").split(" ").filter(Boolean).slice(0, 3);
  const pathLearningMap = new Map<string, { eff: number; count: number }>();

  for (const token of tokens) {
    const rows = db.$client.prepare(`
      SELECT action_sequence, AVG(efficiency) as eff, COUNT(*) as cnt
      FROM completed_sequences
      WHERE goal_tokens LIKE ?
      GROUP BY action_sequence
      ORDER BY eff DESC
      LIMIT 3
    `).all(`%${token}%`) as Array<{ action_sequence: string; eff: number; cnt: number }>;

    for (const row of rows) {
      const existing = pathLearningMap.get(row.action_sequence) ?? { eff: 0, count: 0 };
      existing.eff += row.eff;
      existing.count += 1;
      pathLearningMap.set(row.action_sequence, existing);
    }
  }

  const pathLearning = Array.from(pathLearningMap.entries())
    .map(([key, val]) => ({
      sequence: key,
      efficiency: Math.round((val.eff / val.count) * 100) / 100,
      samples: val.count,
    }))
    .sort((a, b) => b.efficiency - a.efficiency)
    .slice(0, 3);

  const completed = phases.filter(p => p.status === "done").length;
  const archives = db.$client.prepare("SELECT COUNT(*) as cnt FROM routes WHERE archived = 1").get() as { cnt: number };
  const pending = db.$client.prepare("SELECT COUNT(*) as cnt FROM calibration_events WHERE synced = 0").get() as { cnt: number };

  return {
    summary: {
      estimated: totalExpected,
      actual: totalActual,
      phasesCompleted: phases.length,
      accuracy,
    },
    deviations,
    accuracyByAction,
    costLearning,
    pathLearning,
    selfDiagnostics: {
      phasesCompleted: completed,
      archivedRoutes: archives.cnt,
    },
    mesh: { shared: phases.length, pending: pending.cnt },
  };
}
