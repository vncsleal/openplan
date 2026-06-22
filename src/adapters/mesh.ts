import type { CalibrationEvent, CostBaseline } from "../core/domain.js";
import { createLogger } from "../core/logger.js";
import type { MeshSync } from "../core/ports.js";
import { BaselinesResponse } from "../core/schemas.js";

const VALID_MATCH_LEVELS = new Set(["exact", "label_keyword", "action"]);

const log = createLogger("mesh");

function outcomeToMesh(outcome: CalibrationEvent["outcome"]): string {
  if (outcome === "completed") return "success";
  if (outcome === "abandoned") return "failure";
  return "partial";
}

export function createMeshSync(meshUrl: string | null, apiKey: string | null): MeshSync {
  const baseUrl = meshUrl ?? "https://api.openplan.cc";

  return {
    async syncCheckpoints(events: CalibrationEvent[]): Promise<boolean> {
      if (!baseUrl) return false;

      try {
        const batch = events.map((e) => ({
          action: e.action,
          phase_label_tokens: e.phaseLabelTokens,
          expected_cost: e.expectedCost,
          actual_cost: e.actualCost,
          outcome: outcomeToMesh(e.outcome),
          session_id: crypto.randomUUID(),
          project_type: e.projectType,
          timestamp: new Date(e.createdAt).getTime() / 1000,
        }));

        const headers: Record<string, string> = {
          "Content-Type": "application/json",
        };
        if (apiKey) headers.Authorization = `Bearer ${apiKey}`;

        const res = await fetch(`${baseUrl}/v1/checkpoints`, {
          method: "POST",
          headers,
          body: JSON.stringify({ events: batch }),
        });

        return res.ok;
      } catch (e) {
        log.warn("Mesh sync failed", e);
        return false;
      }
    },

    async fetchBaselines(): Promise<CostBaseline[] | null> {
      if (!baseUrl) return [];

      try {
        const headers: Record<string, string> = {
          "Content-Type": "application/json",
        };
        if (apiKey) headers.Authorization = `Bearer ${apiKey}`;

        const res = await fetch(`${baseUrl}/v1/baselines`, { headers });
        if (!res.ok) return null;

        const body: unknown = await res.json();
        const parsed = BaselinesResponse.parse(body);
        const rawBaselines = Array.isArray(parsed) ? parsed : parsed.baselines;

        return rawBaselines.map((b) => {
          const rawMatch = typeof b.match_level === "string" ? b.match_level : "";
          const matchLevel: CostBaseline["matchLevel"] = VALID_MATCH_LEVELS.has(rawMatch)
            ? (rawMatch as CostBaseline["matchLevel"])
            : "action";
          return {
            id: crypto.randomUUID(),
            matchLevel,
            action: typeof b.action === "string" ? b.action : "",
            avgCost: typeof b.cost_tokens === "number" ? b.cost_tokens : typeof b.p50 === "number" ? b.p50 : 0,
            ciLo: typeof b.p25 === "number" ? b.p25 : null,
            ciHi: typeof b.p75 === "number" ? b.p75 : null,
            sampleCount: typeof b.sample_count === "number" ? b.sample_count : 0,
            createdAt: new Date().toISOString(),
          };
        });
      } catch (e) {
        log.warn("Baseline fetch failed", e);
        return [];
      }
    },

    async isReachable(): Promise<boolean> {
      if (!baseUrl) return false;

      try {
        const res = await fetch(`${baseUrl}/v1/health`, {
          signal: AbortSignal.timeout(3000),
        });
        return res.ok;
      } catch (e) {
        log.debug("Mesh unreachable", e);
        return false;
      }
    },
  };
}
