import type { CostBaseline, CalibrationEvent } from "../core/domain.js";
import type { MeshSync } from "../core/ports.js";

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
          session_id: e.routeId ?? "",
          project_type: "software",
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
        console.error(`[openplan] Mesh sync failed: ${e instanceof Error ? e.message : "unknown error"}`);
        return false;
      }
    },

    async fetchBaselines(): Promise<CostBaseline[]> {
      if (!baseUrl) return [];

      try {
        const headers: Record<string, string> = {
          "Content-Type": "application/json",
        };
        if (apiKey) headers.Authorization = `Bearer ${apiKey}`;

        const res = await fetch(`${baseUrl}/v1/baselines`, { headers });
        if (!res.ok) return [];

        const body = (await res.json()) as Record<string, unknown>;
        const rawBaselines = (Array.isArray(body) ? body : (body.baselines as Record<string, unknown>[])) ?? [];

        return rawBaselines.map((b: Record<string, unknown>) => ({
          id: crypto.randomUUID(),
          matchLevel: (b.match_level as CostBaseline["matchLevel"]) ?? "action",
          action: (b.action as string) ?? "",
          avgCost: (b.cost_tokens ?? b.p50 ?? 0) as number,
          ciLo: (b.p25 ?? null) as number | null,
          ciHi: (b.p75 ?? null) as number | null,
          sampleCount: (b.sample_count ?? 0) as number,
          createdAt: new Date().toISOString(),
        }));
      } catch (e) {
        console.error(`[openplan] Baseline fetch failed: ${e instanceof Error ? e.message : "unknown error"}`);
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
        console.error(`[openplan] Mesh unreachable: ${e instanceof Error ? e.message : "unknown error"}`);
        return false;
      }
    },
  };
}
