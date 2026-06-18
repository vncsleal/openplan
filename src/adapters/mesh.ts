import Database from "better-sqlite3";
import type { CalibrationEvent, MeshBaseline } from "../core/ports.js";

export class MeshAdapter {
  private apiUrl: string;
  private apiKey: string;

  constructor(apiUrl: string, apiKey: string) {
    this.apiUrl = apiUrl;
    this.apiKey = apiKey;
  }

  async syncPending(sqlite: Database.Database): Promise<{ synced: number; pending: number }> {
    const pending = sqlite.prepare(
      "SELECT * FROM calibration_events WHERE synced = 0 LIMIT 100"
    ).all() as Array<{
      id: number; action: string; phase_label_tokens: string;
      expected_cost: number; actual_cost: number; outcome: string;
    }>;

    if (pending.length === 0) return { synced: 0, pending: 0 };

    const payload = pending.map(p => ({
      action: p.action,
      phaseLabelTokens: p.phase_label_tokens,
      expectedCost: p.expected_cost,
      actualCost: p.actual_cost,
      outcome: p.outcome,
    }));

    try {
      const response = await fetch(`${this.apiUrl}/v1/checkpoints`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(this.apiKey ? { Authorization: `Bearer ${this.apiKey}` } : {}),
        },
        body: JSON.stringify({ checkpoints: payload }),
      });

      if (response.ok) {
        const ids = pending.map(p => p.id);
        sqlite.prepare(`UPDATE calibration_events SET synced = 1 WHERE id IN (${ids.join(",")})`).run();
        return { synced: pending.length, pending: 0 };
      }
    } catch {
      // Network error — will retry next cycle
    }

    return { synced: 0, pending: pending.length };
  }

  async pullBaselines(): Promise<MeshBaseline[]> {
    if (!this.apiUrl) return [];

    try {
      const response = await fetch(`${this.apiUrl}/v1/baselines`);
      if (response.ok) {
        return await response.json() as MeshBaseline[];
      }
    } catch {
      // Network error — use cached baselines
    }

    return [];
  }

  updateBaselines(sqlite: Database.Database, baselines: MeshBaseline[]): void {
    if (baselines.length === 0) return;

    const now = new Date().toISOString();
    const stmt = sqlite.prepare(`
      INSERT OR REPLACE INTO cost_baselines (match_level, action, phase_label_tokens, avg_cost, ci_lo, ci_hi, sample_count, success_rate, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);

    for (const b of baselines) {
      stmt.run(b.matchLevel, b.action, b.phaseLabelTokens, b.avgCost, b.ciLo, b.ciHi, b.sampleCount, b.successRate, now);
    }
  }
}
