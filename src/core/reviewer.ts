import Database from "better-sqlite3";
import type { ReviewResult } from "./ports.js";

export function reviewRoute(
  sqlite: Database.Database,
  routeId?: string,
  project?: string,
): ReviewResult | { error: string } {
  // Find the route
  let route: Record<string, unknown> | undefined;

  if (routeId) {
    route = sqlite.prepare("SELECT * FROM routes WHERE id = ?").get(routeId) as Record<string, unknown> | undefined;
  } else if (project) {
    route = sqlite.prepare(
      "SELECT * FROM routes WHERE project = ? AND archived = 0 ORDER BY created_at DESC LIMIT 1"
    ).get(project) as Record<string, unknown> | undefined;
  } else {
    route = sqlite.prepare(
      "SELECT * FROM routes WHERE archived = 0 ORDER BY created_at DESC LIMIT 1"
    ).get() as Record<string, unknown> | undefined;
  }

  if (!route) return { error: "no route found" };

  const phases = sqlite.prepare(
    "SELECT * FROM route_phases WHERE route_id = ? AND status = 'done' ORDER BY sequence"
  ).all(route.id as string) as Array<{
    label: string; action: string; expected_cost: number; actual_cost: number | null;
    outcome: string | null; status: string;
  }>;

  if (phases.length === 0) return { error: "no completed phases" };

  const totalExpected = phases.reduce((s, p) => s + p.expected_cost, 0);
  const totalActual = phases.reduce((s, p) => s + (p.actual_cost ?? 0), 0);

  // Compute accuracy (protected against zero)
  const accuracy = (totalExpected > 0 && totalActual > 0)
    ? Math.round(Math.min(totalActual / totalExpected, totalExpected / totalActual) * 100) / 100
    : 0;

  // Per-phase deviations
  const deviations = phases.map(p => ({
    phase: p.label,
    expected: p.expected_cost,
    actual: p.actual_cost ?? 0,
    ratio: p.expected_cost > 0 ? Math.round(((p.actual_cost ?? 0) / p.expected_cost) * 100) / 100 : 0,
  }));

  // Accuracy by action
  const actionMap = new Map<string, { sum: number; count: number }>();
  for (const p of phases) {
    const entry = actionMap.get(p.action) ?? { sum: 0, count: 0 };
    if (p.actual_cost && p.expected_cost > 0) {
      entry.sum += p.actual_cost / p.expected_cost;
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

  // Cost learning
  const costLearning = (() => {
    const rows = sqlite.prepare(`
      SELECT action, AVG(actual_cost) as avg_cost, COUNT(*) as samples
      FROM calibration_events
      GROUP BY action
      ORDER BY samples DESC
      LIMIT 5
    `).all() as Array<{ action: string; avg_cost: number; samples: number }>;
    return rows.map(r => ({ action: r.action, avgCost: Math.round(r.avg_cost), samples: r.samples }));
  })();

  // Path learning — save completed sequence
  {
    const actionSeq = phases.map(p => p.action).join(",");
    const efficiency = totalExpected > 0 ? totalActual / totalExpected : 1;

    const existing = sqlite.prepare(
      "SELECT COUNT(*) as cnt FROM completed_sequences WHERE goal_tokens = ? AND action_sequence = ?"
    ).get((route.goal_tokens as string) ?? "", actionSeq) as { cnt: number };

    if (existing.cnt === 0) {
      sqlite.prepare(`
        INSERT INTO completed_sequences (goal_tokens, context_tokens, action_sequence, total_expected, total_actual, efficiency, outcome, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'success', ?)
      `).run(
        route.goal_tokens ?? "",
        route.context_tokens ?? "",
        actionSeq,
        totalExpected,
        totalActual,
        efficiency,
        new Date().toISOString(),
      );
    }
  }

  // Path learning — find similar
  const pathLearning = (() => {
    const tokens = ((route.goal_tokens as string) ?? "").split(" ").filter(Boolean).slice(0, 3);
    if (tokens.length === 0) return [];

    const results: Map<string, { totalEff: number; count: number }> = new Map();
    for (const token of tokens) {
      const rows = sqlite.prepare(`
        SELECT action_sequence, AVG(efficiency) as eff, COUNT(*) as cnt
        FROM completed_sequences
        WHERE goal_tokens LIKE ?
        GROUP BY action_sequence
        ORDER BY eff ASC LIMIT 3
      `).all(`%${token}%`) as Array<{ action_sequence: string; eff: number; cnt: number }>;

      for (const row of rows) {
        const existing = results.get(row.action_sequence) ?? { totalEff: 0, count: 0 };
        existing.totalEff += row.eff;
        existing.count += 1;
        results.set(row.action_sequence, existing);
      }
    }

    return Array.from(results.entries())
      .map(([key, val]) => ({
        sequence: key,
        efficiency: Math.round((val.totalEff / val.count) * 100) / 100,
        samples: val.count,
      }))
      .sort((a, b) => b.efficiency - a.efficiency)
      .slice(0, 3);
  })();

  // Self-diagnostics
  const completed = phases.filter(p => p.status === "done").length;
  const hazardsFired = sqlite.prepare("SELECT COUNT(*) as cnt FROM calibration_events WHERE outcome = 'success'").get() as { cnt: number };
  const archives = sqlite.prepare("SELECT COUNT(*) as cnt FROM routes WHERE archived = 1").get() as { cnt: number };

  const selfDiagnostics: Record<string, unknown> = {
    phasesCompleted: completed,
    archivedRoutes: archives.cnt,
  };

  // Pending sync count
  const pending = sqlite.prepare("SELECT COUNT(*) as cnt FROM calibration_events WHERE synced = 0").get() as { cnt: number };

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
    selfDiagnostics,
    mesh: { shared: phases.length, pending: pending.cnt },
  };
}
