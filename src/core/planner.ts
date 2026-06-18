import Database from "better-sqlite3";
import { tokenize, estimateCost } from "./costs.js";
import type { Phase } from "./ports.js";

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
  sqlite: Database.Database,
  goal: string,
  context: string = "",
  replan: boolean = false,
  apiKey?: string,
): Record<string, unknown> {
  const goalTokens = tokenize(goal);
  const contextTokens = tokenize(context);

  // If replan, archive current active route
  if (replan) {
    const active = sqlite.prepare(
      "SELECT id FROM routes WHERE status = 'active' AND archived = 0 ORDER BY created_at DESC LIMIT 1"
    ).get() as { id: string } | undefined;

    if (active) {
      sqlite.prepare("UPDATE routes SET archived = 1, status = 'archived' WHERE id = ?").run(active.id);
    }
  }

  // Generate phases
  const phases = generatePhases(sqlite, goalTokens, contextTokens);
  const totalCost = phases.reduce((sum, p) => sum + p.expectedCost, 0);

  // Create route
  const routeId = `R-${Math.random().toString(36).substring(2, 8).toUpperCase()}`;
  const now = new Date().toISOString();
  const project = inferProject();

  // Check for existing active route (idempotent)
  const existing = sqlite.prepare(
    "SELECT id FROM routes WHERE project = ? AND goal = ? AND archived = 0 AND status = 'active' ORDER BY created_at DESC LIMIT 1"
  ).get(project, goal) as { id: string } | undefined;

  if (existing && !replan) {
    // Return existing route
    const existingPhases = sqlite.prepare(
      "SELECT label, action, expected_cost, status FROM route_phases WHERE route_id = ? ORDER BY sequence"
    ).all(existing.id) as Array<{ label: string; action: string; expected_cost: number; status: string }>;

    return {
      route: {
        id: existing.id,
        phases: existingPhases.map(p => ({
          label: p.label,
          action: p.action,
          expectedCost: p.expected_cost,
          status: p.status,
        })),
      },
    };
  }

  sqlite.prepare(`
    INSERT INTO routes (id, project, goal, context, total_expected, status, goal_tokens, context_tokens, created_at)
    VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
  `).run(routeId, project, goal, context, totalCost, goalTokens, contextTokens, now);

  for (let i = 0; i < phases.length; i++) {
    const p = phases[i];
    const phaseId = `P-${Math.random().toString(36).substring(2, 10).toUpperCase()}`;
    const labelTokens = tokenize(p.label);
    sqlite.prepare(`
      INSERT INTO route_phases (id, route_id, label, action, expected_cost, status, sequence, label_tokens, created_at)
      VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
    `).run(phaseId, routeId, p.label, p.action, p.expectedCost, i, labelTokens, now);
  }

  // Archive old routes for same project
  const oldRoutes = sqlite.prepare(
    "SELECT id FROM routes WHERE project = ? AND id != ? AND archived = 0 AND status = 'active'"
  ).all(project, routeId) as Array<{ id: string }>;

  for (const old of oldRoutes) {
    sqlite.prepare("UPDATE routes SET archived = 1, status = 'archived' WHERE id = ?").run(old.id);
    sqlite.prepare("UPDATE routes SET abandon_reason = 'superseded by new plan' WHERE id = ?").run(old.id);
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
    personalBias: apiKey ? computePersonalBias(sqlite, apiKey) : undefined,
  };
}

function generatePhases(
  sqlite: Database.Database,
  goalTokens: string,
  _contextTokens: string,
): Phase[] {
  // Try to find matching completed sequences
  const sequences = findMatchingSequences(sqlite, goalTokens);

  if (sequences.length > 0) {
    const best = sequences[0];
    const actions = best.actionSequence.split(",");
    const labels = LABEL_TEMPLATES[actions.length] ?? [];
    return actions.map((action, i) => {
      const label = labels[i]?.label ?? `Phase ${i + 1}`;
      const est = estimateCost(sqlite, action.trim(), label);
      return { label, action: action.trim(), expectedCost: est.expectedCost, ci: [est.ciLo, est.ciHi] as [number, number] };
    });
  }

  // Fall back to defaults
  return DEFAULT_PHASE_TEMPLATES.map(t => {
    const est = estimateCost(sqlite, t.action, t.label);
    return { label: t.label, action: t.action, expectedCost: est.expectedCost, ci: [est.ciLo, est.ciHi] as [number, number] };
  });
}

function findMatchingSequences(
  sqlite: Database.Database,
  goalTokens: string,
  _contextTokens?: string,
): Array<{ actionSequence: string; avgEfficiency: number; samples: number }> {
  if (!goalTokens) return [];

  const tokens = goalTokens.split(" ").filter(Boolean).slice(0, 5);
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
      const existing = results.get(row.action_sequence);
      if (existing) {
        existing.totalEff += row.eff;
        existing.count += 1;
      } else {
        results.set(row.action_sequence, { totalEff: row.eff, count: 1 });
      }
    }
  }

  // Sort by efficiency descending (higher = better)
  return Array.from(results.entries())
    .map(([key, val]) => ({
      actionSequence: key,
      avgEfficiency: Math.round((val.totalEff / val.count) * 100) / 100,
      samples: val.count,
    }))
    .sort((a, b) => b.avgEfficiency - a.avgEfficiency);
}

function computePersonalBias(sqlite: Database.Database, apiKey: string): { ratio: number; basedOn: number } {
  const row = sqlite.prepare(`
    SELECT AVG(actual_cost / expected_cost) as bias, COUNT(*) as cnt
    FROM calibration_events
    WHERE api_key = ? AND expected_cost > 0
  `).get(apiKey) as { bias: number | null; cnt: number };

  if (row.bias !== null && row.cnt >= 3) {
    return { ratio: Math.round(row.bias * 100) / 100, basedOn: row.cnt };
  }
  return { ratio: 1, basedOn: 0 };
}

import { readFileSync, existsSync } from "fs";
import { join } from "path";

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
