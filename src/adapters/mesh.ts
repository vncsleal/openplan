import type { CostBaseline, CalibrationEvent } from "../core/domain.js";
import type { MeshSync } from "../core/ports.js";

export function createMeshSync(meshUrl: string | null, apiKey: string | null): MeshSync {
  const baseUrl = meshUrl ?? "https://api.openplan.cc";

  return {
    async syncCheckpoints(events: CalibrationEvent[]): Promise<boolean> {
      if (!meshUrl) return false;

      try {
        const batch = events.map((e) => ({
          action: e.action,
          phase_label_tokens: e.phaseLabelTokens,
          expected_cost: e.expectedCost,
          actual_cost: e.actualCost,
          outcome: e.outcome,
          session_id: e.routeId,
          project_type: "software",
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
      if (!meshUrl) return [];

      try {
        const headers: Record<string, string> = {
          "Content-Type": "application/json",
        };
        if (apiKey) headers.Authorization = `Bearer ${apiKey}`;

        const res = await fetch(`${baseUrl}/v1/baselines`, { headers });
        if (!res.ok) return [];

        const data = (await res.json()) as Record<string, unknown>[];
        return data.map((b: Record<string, unknown>) => ({
          id: crypto.randomUUID(),
          matchLevel: mapMatchLevel(b.match_level as string | undefined),
          action: (b.action as string) ?? "",
          avgCost: (b.avgCost ?? b.p50 ?? 0) as number,
          ciLo: (b.ciLo ?? b.p25 ?? null) as number | null,
          ciHi: (b.ciHi ?? b.p75 ?? null) as number | null,
          sampleCount: (b.sample_count ?? b.sampleCount ?? 0) as number,
          createdAt: new Date().toISOString(),
        }));
      } catch (e) {
        console.error(`[openplan] Baseline fetch failed: ${e instanceof Error ? e.message : "unknown error"}`);
        return [];
      }
    },

    async isReachable(): Promise<boolean> {
      if (!meshUrl) return false;

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

function mapMatchLevel(level: string | undefined): CostBaseline["matchLevel"] {
  if (level === "exact") return "exact";
  if (level === "label_keyword" || level === "label") return "label_keyword";
  return "action";
}
