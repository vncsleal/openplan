import Database from "better-sqlite3";
import { deriveOutcome, estimateCost, tokenize } from "./costs.js";
import type { CheckpointResult, Hazard } from "./ports.js";

export function checkpointPhase(
  sqlite: Database.Database,
  routeId: string,
  phaseLabel: string,
  actualCost: number,
  apiKey?: string,
): CheckpointResult {
  // Find the phase
  const route = sqlite.prepare("SELECT id, total_expected FROM routes WHERE id = ?").get(routeId) as { id: string; total_expected: number } | undefined;
  if (!route) throw new Error(`Route ${routeId} not found`);

  // Find matching phase (exact label first, then substring)
  let phase = sqlite.prepare(
    "SELECT * FROM route_phases WHERE route_id = ? AND label = ? AND status = 'pending' LIMIT 1"
  ).get(routeId, phaseLabel) as { id: string; label: string; action: string; expected_cost: number; sequence: number } | undefined;

  if (!phase) {
    // Try substring match (subsumption)
    const matches = sqlite.prepare(
      "SELECT * FROM route_phases WHERE route_id = ? AND status = 'pending' ORDER BY sequence LIMIT 5"
    ).all(routeId) as Array<{ id: string; label: string; action: string; expected_cost: number; sequence: number }>;

    for (const p of matches) {
      const prefix = p.label.split("(")[0].trim();
      if (phaseLabel.startsWith(prefix) || phaseLabel.includes(p.label)) {
        phase = p;
        break;
      }
    }
  }

  if (!phase) throw new Error(`No pending phase matching "${phaseLabel}" found in route ${routeId}`);

  // Compute deviation
  const outcome = deriveOutcome(phase.expected_cost, actualCost);
  const deviationRatio = Math.round((actualCost / phase.expected_cost) * 100) / 100;

  // Update phase
  sqlite.prepare(
    "UPDATE route_phases SET status = 'done', actual_cost = ?, outcome = ? WHERE id = ?"
  ).run(actualCost, outcome, phase.id);

  // Insert calibration event
  const labelTokens = tokenize(phaseLabel);
  sqlite.prepare(`
    INSERT INTO calibration_events (action, phase_label_tokens, expected_cost, actual_cost, outcome, api_key, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
  `).run(phase.action, labelTokens, phase.expected_cost, actualCost, outcome, apiKey ?? null, new Date().toISOString());

  // Update route total
  sqlite.prepare("UPDATE routes SET total_actual = COALESCE(total_actual, 0) + ? WHERE id = ?").run(actualCost, routeId);

  // Find next phase
  const nextPhase = sqlite.prepare(
    "SELECT label, action, expected_cost FROM route_phases WHERE route_id = ? AND status = 'pending' ORDER BY sequence LIMIT 1"
  ).get(routeId) as { label: string; action: string; expected_cost: number } | undefined;

  // Check if route is complete
  const remaining = sqlite.prepare(
    "SELECT COUNT(*) as cnt FROM route_phases WHERE route_id = ? AND status != 'done'"
  ).get(routeId) as { cnt: number };

  const routeCompleted = remaining.cnt === 0;
  if (routeCompleted) {
    sqlite.prepare("UPDATE routes SET status = 'completed', completed_at = ? WHERE id = ?").run(new Date().toISOString(), routeId);
  }

  // Generate hazards
  const hazards = generateHazards(sqlite, routeId, nextPhase?.label);

  return {
    phaseCompleted: phase.label,
    actualCost,
    expectedCost: phase.expected_cost,
    deviation: { ratio: deviationRatio, level: deviationRatio <= 1.3 ? "low" : deviationRatio <= 2.0 ? "medium" : "high", outcome },
    nextPhase: nextPhase ? { label: nextPhase.label, expectedCost: nextPhase.expected_cost, ci: [0, 0] } : null,
    hazards,
    routeCompleted,
  };
}

export function getRouteStatus(sqlite: Database.Database, routeId: string): Record<string, unknown> {
  const route = sqlite.prepare("SELECT * FROM routes WHERE id = ?").get(routeId) as Record<string, unknown> | undefined;
  if (!route) return { error: "route not found" };

  const phases = sqlite.prepare(
    "SELECT label, action, expected_cost, actual_cost, outcome, status, sequence FROM route_phases WHERE route_id = ? ORDER BY sequence"
  ).all(routeId) as Array<Record<string, unknown>>;

  const doneCount = phases.filter(p => p.status === "done").length;

  return {
    routeId,
    project: route.project,
    status: route.status,
    phases,
    position: `${doneCount}/${phases.length}`,
  };
}

function generateHazards(sqlite: Database.Database, routeId: string, nextLabel?: string): Hazard[] {
  const hazards: Hazard[] = [];

  // Variance-based hazards
  if (nextLabel) {
    const nextTokens = tokenize(nextLabel);
    if (nextTokens) {
      const tokens = nextTokens.split(" ");
      for (const token of tokens) {
        const row = sqlite.prepare(`
          SELECT COUNT(*) as cnt, AVG(actual_cost) as avg, MAX(actual_cost) as maxi, MIN(actual_cost) as mini
          FROM calibration_events
          WHERE phase_label_tokens LIKE ? AND action IS NOT NULL
          HAVING cnt >= 3
        `).get(`%${token}%`) as { cnt: number; avg: number | null; maxi: number | null; mini: number | null } | undefined;

        if (row && row.avg && row.maxi && row.mini && row.maxi / Math.max(row.mini, 1) > 3) {
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


