import type { CostBaseline, CalibrationEvent, RoutePhase } from "./domain.js";

export function calculateDeviation(actual: number, expected: number): number {
  if (expected === 0) return actual > 0 ? Number.POSITIVE_INFINITY : 0;
  return actual - expected;
}

export function deviationLabel(
  deviation: number | null,
  expected: number | null,
): "under" | "over" | "on_track" | null {
  if (deviation === null || expected === null || expected === 0) return null;
  const ratio = Math.abs(deviation) / expected;
  if (ratio < 0.1) return "on_track";
  return deviation < 0 ? "under" : "over";
}

export function personalBias(events: CalibrationEvent[]): number | null {
  const ratios = events.filter((e) => e.expectedCost > 0).map((e) => e.actualCost / e.expectedCost);

  if (ratios.length === 0) return null;

  const sum = ratios.reduce((a, b) => a + b, 0);
  const mean = sum / ratios.length;
  return Number(mean.toFixed(3));
}

export function accuracyByAction(events: CalibrationEvent[]): {
  action: string;
  sampleCount: number;
  meanDeviation: number | null;
  mape: number | null;
}[] {
  const byAction = new Map<string, number[]>();
  const expectedByAction = new Map<string, number[]>();

  for (const event of events) {
    if (event.expectedCost <= 0) continue;
    const dev = event.actualCost - event.expectedCost;
    const existing = byAction.get(event.action) ?? [];
    existing.push(dev);
    byAction.set(event.action, existing);

    const expExisting = expectedByAction.get(event.action) ?? [];
    expExisting.push(event.expectedCost);
    expectedByAction.set(event.action, expExisting);
  }

  return [...byAction.entries()]
    .map(([action, deviations]) => {
      const expectedList = expectedByAction.get(action) ?? [];
      const sumDev = deviations.reduce((a, b) => a + b, 0);
      const meanDev = sumDev / deviations.length;
      const apeSum = deviations.reduce((acc, dev, i) => {
        const exp = expectedList[i];
        return acc + (exp > 0 ? Math.abs(dev) / exp : 0);
      }, 0);
      const mape = expectedList.length > 0 ? (apeSum / expectedList.length) * 100 : null;

      return {
        action,
        sampleCount: deviations.length,
        meanDeviation: Number(meanDev.toFixed(2)),
        mape: mape !== null ? Number(mape.toFixed(1)) : null,
      };
    })
    .sort((a, b) => b.sampleCount - a.sampleCount);
}

export function applyPersonalBias(expected: number, bias: number | null): number {
  if (bias === null || bias <= 0) return expected;
  return Math.round(expected * bias);
}

export function ciFromBaseline(
  baselines: CostBaseline[],
  goalTokens: string,
  labelTokens: string,
  action: string,
): { expected: number; ci: { lo: number; hi: number } | null } | null {
  const goalSet = new Set(goalTokens.split(/\s+/));
  const labelSet = new Set(labelTokens.split(/\s+/));

  const exact = baselines.filter((b) => b.matchLevel === "exact" && b.action === action);
  for (const b of exact) {
    const overlap = [...goalSet].filter((t) => labelSet.has(t)).length;
    if (overlap >= 2 && b.sampleCount >= 5) {
      return {
        expected: b.avgCost,
        ci: b.ciLo !== null && b.ciHi !== null ? { lo: b.ciLo, hi: b.ciHi } : null,
      };
    }
  }

  const labelKeyword = baselines.filter((b) => b.matchLevel === "label_keyword" && b.action === action);
  for (const b of labelKeyword) {
    if (b.sampleCount >= 20) {
      const overlap = [...labelSet].filter((t) => goalSet.has(t)).length;
      if (overlap >= 1) {
        return {
          expected: b.avgCost,
          ci: b.ciLo !== null && b.ciHi !== null ? { lo: b.ciLo, hi: b.ciHi } : null,
        };
      }
    }
  }

  const actionFallback = baselines.find((b) => b.matchLevel === "action" && b.action === action);
  if (actionFallback) {
    return {
      expected: actionFallback.avgCost,
      ci:
        actionFallback.ciLo !== null && actionFallback.ciHi !== null
          ? { lo: actionFallback.ciLo, hi: actionFallback.ciHi }
          : null,
    };
  }

  return null;
}

export function efficiency(phases: RoutePhase[]): number | null {
  const completed = phases.filter((p) => p.status === "completed");
  const totalExpected = completed.reduce((sum, p) => sum + (p.expectedCost ?? 0), 0);
  const totalActual = completed.reduce((sum, p) => sum + (p.actualCost ?? 0), 0);

  if (totalExpected === 0) return null;
  if (totalActual === 0) return 0;

  return Number((totalExpected / totalActual).toFixed(3));
}

export function hazardFromPhases(phases: RoutePhase[]): string[] {
  const hazards: string[] = [];

  for (const phase of phases) {
    if (phase.actualCost === null || phase.expectedCost === null) continue;
    if (phase.expectedCost === 0) continue;
    const ratio = phase.actualCost / phase.expectedCost;
    if (ratio > 3.0) {
      hazards.push(
        `High variance detected in phase "${phase.label}": actual ${phase.actualCost} vs expected ${phase.expectedCost} (${ratio.toFixed(1)}x)`,
      );
    }
  }

  return hazards;
}
