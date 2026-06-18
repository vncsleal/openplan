import type Database from "better-sqlite3";

const STOP_WORDS = new Set([
  "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
  "have", "has", "had", "do", "does", "did", "will", "would", "could",
  "should", "may", "might", "shall", "can", "need", "dare", "ought",
  "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
  "into", "through", "during", "before", "after", "above", "below",
  "between", "out", "off", "over", "under", "again", "further",
  "then", "once", "here", "there", "when", "where", "why", "how",
  "all", "each", "every", "both", "few", "more", "most", "other",
  "some", "such", "no", "nor", "not", "only", "own", "same", "so",
  "than", "too", "very", "just", "because", "but", "and", "or",
  "if", "while", "that", "this", "these", "those", "it", "its",
]);

export function tokenize(text: string): string {
  const cleaned = text.toLowerCase().replace(/[^a-z0-9\s]/g, " ");
  const tokens = cleaned.split(/\s+/).filter(Boolean);
  return tokens.filter(t => t.length >= 3 && !STOP_WORDS.has(t)).join(" ");
}

export function deriveOutcome(expected: number, actual: number): string {
  const ratio = expected > 0 ? actual / expected : 1;
  if (ratio <= 1.3) return "success";
  if (ratio <= 2.0) return "partial";
  return "failure";
}

export interface CostEstimate {
  expectedCost: number;
  ciLo: number;
  ciHi: number;
  matchLevel: string;
  samples: number;
}

export function estimateCost(
  sqlite: Database.Database,
  action: string,
  phaseLabel: string,
): CostEstimate {
  const labelTokens = tokenize(phaseLabel);

  // Level 1: Phase label keyword match
  if (labelTokens) {
    const tokens = labelTokens.split(" ");
    for (const token of tokens) {
      const rows = sqlite.prepare(`
        SELECT AVG(actual_cost) as avg_cost, COUNT(*) as samples
        FROM calibration_events
        WHERE phase_label_tokens LIKE ? AND action = ?
        HAVING COUNT(*) >= 5
      `).all(`%${token}%`, action) as Array<{ avg_cost: number | null; samples: number }>;

      if (rows.length > 0 && rows[0].avg_cost !== null) {
        const clo = rows[0].avg_cost * 0.7;
        const chi = rows[0].avg_cost * 1.3;
        return { expectedCost: Math.round(rows[0].avg_cost), ciLo: Math.round(clo), ciHi: Math.round(chi), matchLevel: "label", samples: rows[0].samples };
      }
    }
  }

  // Level 2: Action fallback from calibration_events
  const actionRows = sqlite.prepare(`
    SELECT AVG(actual_cost) as avg_cost, COUNT(*) as samples
    FROM calibration_events
    WHERE action = ?
  `).all(action) as Array<{ avg_cost: number | null; samples: number }>;

  if (actionRows.length > 0 && actionRows[0].avg_cost !== null && actionRows[0].samples >= 3) {
    const clo = actionRows[0].avg_cost * 0.5;
    const chi = actionRows[0].avg_cost * 1.5;
    return { expectedCost: Math.round(actionRows[0].avg_cost), ciLo: Math.round(clo), ciHi: Math.round(chi), matchLevel: "action", samples: actionRows[0].samples };
  }

  // Level 3: Bundled defaults
  const defaultRow = sqlite.prepare(`
    SELECT avg_cost, ci_lo, ci_hi, sample_count FROM cost_baselines
    WHERE match_level = 'default' AND action = ?
    LIMIT 1
  `).get(action) as { avg_cost: number; ci_lo: number; ci_hi: number; sample_count: number } | undefined;

  if (defaultRow) {
    return {
      expectedCost: Math.round(defaultRow.avg_cost),
      ciLo: Math.round(defaultRow.ci_lo),
      ciHi: Math.round(defaultRow.ci_hi),
      matchLevel: "default",
      samples: defaultRow.sample_count,
    };
  }

  // Ultimate fallback
  return { expectedCost: 2000, ciLo: 500, ciHi: 5000, matchLevel: "fallback", samples: 0 };
}

export function computePersonalBias(sqlite: Database.Database, apiKey?: string): { ratio: number; basedOn: number } {
  if (!apiKey) return { ratio: 1, basedOn: 0 };

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
