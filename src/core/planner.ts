import { eq, and, like, desc, sql } from "drizzle-orm";
import { routes, routePhases, completedSequences, calibrationEvents } from "../db/schema.js";
import type { Db } from "../db/connection.js";
import { tokenize, estimateCost } from "./costs.js";
import type { Phase } from "./ports.js";
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

const DEFAULT_PHASE_TEMPLATES: Array<{ label: string; action: string }> = [
  { label: "Scaffold + setup", action: "implement" },
  { label: "Core implementation", action: "implement" },
  { label: "Integration + test", action: "test" },
  { label: "Deploy to production", action: "deploy" },
];

const LABEL_TEMPLATES: Record<number, Array<{ label: string; action: string }>> = {
  2: [{ label: "Phase 1 — Setup", action: "implement" }, { label: "Phase 2 — Deliver", action: "deploy" }],
  3: [{ label: "Scaffold + setup", action: "implement" }, { label: "Core logic", action: "implement" }, { label: "Deploy", action: "deploy" }],
  4: [{ label: "Scaffold + setup", action: "implement" }, { label: "Core implementation", action: "implement" }, { label: "Integration + test", action: "test" }, { label: "Deploy", action: "deploy" }],
};

export function planProject(
  db: Db,
  goal: string,
  context: string = "",
  replan: boolean = false,
  apiKey?: string,
): Record<string, unknown> {
  const goalTokens = tokenize(goal);
  const contextTokens = tokenize(context);
  const now = new Date().toISOString();
  const project = inferProject();

  if (replan) {
    db.update(routes)
      .set({ archived: true, status: "archived" })
      .where(and(eq(routes.status, "active"), eq(routes.archived, false)))
      .run();
  }

  const existing = db.select({ id: routes.id })
    .from(routes)
    .where(and(
      eq(routes.project, project),
      eq(routes.goal, goal),
      eq(routes.archived, false),
      eq(routes.status, "active"),
    ))
    .orderBy(desc(routes.createdAt))
    .limit(1)
    .get();

  if (existing && !replan) {
    const existingPhases = db.select({
      label: routePhases.label,
      action: routePhases.action,
      expectedCost: routePhases.expectedCost,
      status: routePhases.status,
    })
      .from(routePhases)
      .where(eq(routePhases.routeId, existing.id))
      .orderBy(routePhases.sequence)
      .all();

    return {
      route: {
        id: existing.id,
        phases: existingPhases.map(p => ({
          label: p.label,
          action: p.action,
          expectedCost: p.expectedCost,
          status: p.status,
        })),
      },
    };
  }

  const phases = generatePhases(db, goalTokens, contextTokens);
  const totalCost = phases.reduce((sum, p) => sum + p.expectedCost, 0);
  const routeId = `R-${Math.random().toString(36).substring(2, 8).toUpperCase()}`;

  db.insert(routes).values({
    id: routeId,
    project,
    goal,
    context,
    totalExpected: totalCost,
    status: "active",
    goalTokens,
    contextTokens,
    createdAt: now,
  }).run();

  for (let i = 0; i < phases.length; i++) {
    const p = phases[i];
    const phaseId = `P-${Math.random().toString(36).substring(2, 10).toUpperCase()}`;
    db.insert(routePhases).values({
      id: phaseId,
      routeId,
      label: p.label,
      action: p.action,
      expectedCost: p.expectedCost,
      status: "pending",
      sequence: i,
      labelTokens: tokenize(p.label),
      createdAt: now,
    }).run();
  }

  const olderRoutes = db.select({ id: routes.id })
    .from(routes)
    .where(and(eq(routes.project, project), eq(routes.archived, false), eq(routes.status, "active")))
    .all();

  for (const old of olderRoutes) {
    if (old.id !== routeId) {
      db.update(routes)
        .set({ archived: true, status: "archived", abandonReason: "superseded by new plan" })
        .where(eq(routes.id, old.id))
        .run();
    }
  }

  return {
    route: {
      id: routeId,
      phases: phases.map(p => ({
        label: p.label,
        action: p.action,
        expectedCost: p.expectedCost,
        ci: p.ci,
        status: "pending",
      })),
      totalCost,
    },
    routeEvidence: {
      basedOn: `phase sequence (${phases.length} phases)`,
    },
    personalBias: apiKey ? computePersonalBiasLegacy(db, apiKey) : undefined,
  };
}

function generatePhases(
  db: Db,
  goalTokens: string,
  _contextTokens: string,
): Phase[] {
  const sequences = findMatchingSequences(db, goalTokens);

  if (sequences.length > 0) {
    const best = sequences[0];
    const actions = best.actionSequence.split(",");
    const labels = LABEL_TEMPLATES[actions.length] ?? [];
    return actions.map((action, i) => {
      const label = labels[i]?.label ?? `Phase ${i + 1}`;
      const est = estimateCost(db, action.trim(), label);
      return { label, action: action.trim(), expectedCost: est.expectedCost, ci: [est.ciLo, est.ciHi] as [number, number] };
    });
  }

  return DEFAULT_PHASE_TEMPLATES.map(t => {
    const est = estimateCost(db, t.action, t.label);
    return { label: t.label, action: t.action, expectedCost: est.expectedCost, ci: [est.ciLo, est.ciHi] as [number, number] };
  });
}

function findMatchingSequences(
  db: Db,
  goalTokens: string,
): Array<{ actionSequence: string; avgEfficiency: number; samples: number }> {
  if (!goalTokens) return [];

  const tokens = goalTokens.split(" ").filter(Boolean).slice(0, 5);
  if (tokens.length === 0) return [];

  const results: Map<string, { totalEff: number; count: number }> = new Map();

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
      const existing = results.get(row.action_sequence);
      if (existing) {
        existing.totalEff += row.eff;
        existing.count += 1;
      } else {
        results.set(row.action_sequence, { totalEff: row.eff, count: 1 });
      }
    }
  }

  return Array.from(results.entries())
    .map(([key, val]) => ({
      actionSequence: key,
      avgEfficiency: Math.round((val.totalEff / val.count) * 100) / 100,
      samples: val.count,
    }))
    .sort((a, b) => b.avgEfficiency - a.avgEfficiency);
}

function computePersonalBiasLegacy(db: Db, apiKey: string): { ratio: number; basedOn: number } {
  const row = db.$client.prepare(`
    SELECT AVG(actual_cost / expected_cost) as bias, COUNT(*) as cnt
    FROM calibration_events
    WHERE api_key = ? AND expected_cost > 0
  `).get(apiKey) as { bias: number | null; cnt: number } | undefined;

  if (row && row.bias !== null && row.cnt >= 3) {
    return { ratio: Math.round(row.bias * 100) / 100, basedOn: row.cnt };
  }
  return { ratio: 1, basedOn: 0 };
}

function inferProject(): string {
  const cwd = process.cwd();
  const openplanPath = join(cwd, ".openplan");
  try {
    if (existsSync(openplanPath)) {
      const data = JSON.parse(readFileSync(openplanPath, "utf-8"));
      if (data.project) return data.project;
    }
  } catch {
    // fall through
  }
  return cwd.split("/").pop() || "default";
}
