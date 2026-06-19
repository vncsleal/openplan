import type { DataStore } from "./ports.js";
import type { ReviewResult, ReviewSummary, PhaseDeviation, StructuredError } from "./domain.js";
import { calculateDeviation, deviationLabel, accuracyByAction, efficiency } from "./costs.js";
import { randomUUID } from "node:crypto";

export interface ReviewInput {
  routeId?: string;
  project?: string;
  identityId: string;
  store: DataStore;
  meshReachable?: boolean;
}

export function review(input: ReviewInput): ReviewResult | StructuredError {
  const { routeId, project, identityId, store, meshReachable } = input;

  const resolved = routeIdResolve(routeId, project, store);
  if (typeof resolved === "object" && "error" in resolved) return resolved;

  const route = store.getRoute(resolved);
  if (!route) return { error: { code: "NOT_FOUND", message: "Route not found" } };

  const phases = store.getPhases(resolved);
  const events = store.getCalibrationEvents();
  const baselines = store.getBaselines();
  const sequences = store.getSequences();
  const allRoutes = store.getRoutesForProject(route.project);
  const unsyncedEvents = store.getUnsyncedCalibrationEvents();

  const completedPhases = phases.filter((p) => p.status === "completed");
  const skippedPhases = phases.filter((p) => p.status === "skipped");
  const totalActual = completedPhases.reduce((s, p) => s + (p.actualCost ?? 0), 0);
  const totalExpected = phases.reduce((s, p) => s + (p.expectedCost ?? 0), 0);
  const overallDeviation = totalExpected > 0 ? totalActual - totalExpected : null;

  const summary: ReviewSummary = {
    routeId: resolved,
    project: route.project,
    goal: route.goal,
    status: route.status,
    phaseCount: phases.length,
    completedCount: completedPhases.length,
    skippedCount: skippedPhases.length,
    totalExpected,
    totalActual,
    overallDeviation,
  };

  const deviations: PhaseDeviation[] = phases.map((p) => {
    const dev =
      p.expectedCost !== null && p.actualCost !== null ? calculateDeviation(p.actualCost, p.expectedCost) : null;
    return {
      label: p.label,
      action: p.action,
      expectedCost: p.expectedCost,
      actualCost: p.actualCost,
      deviation: dev !== null ? Number(dev.toFixed(2)) : null,
      deviationLabel: deviationLabel(dev, dev !== null ? p.expectedCost : null),
      outcome: p.status === "completed" ? "completed" : p.status === "skipped" ? "abandoned" : "modified",
    };
  });

  const accuracy = accuracyByAction(events);

  const costLearning = baselines.map((b) => ({
    matchLevel: b.matchLevel,
    action: b.action,
    avgCost: b.avgCost,
    sampleCount: b.sampleCount,
  }));

  const pathLearning = sequences.map((s) => ({
    actionSequence: s.actionSequence,
    efficiency: s.efficiency,
    totalExpected: s.totalExpected,
    totalActual: s.totalActual,
  }));

  // Save completed sequence for path learning
  if (route.status === "completed") {
    const eff = efficiency(phases);
    if (eff !== null) {
      const actionSeq = phases
        .filter((p) => p.status === "completed")
        .map((p) => p.action)
        .join(",");
      store.addSequence({
        id: randomUUID(),
        actionSequence: actionSeq,
        totalExpected,
        totalActual,
        efficiency: eff,
        createdAt: new Date().toISOString(),
      });
    }
  }

  const archivedCount = allRoutes.filter((r) => r.status === "archived").length;
  const totalRouteCount = allRoutes.length;
  const archiveRate = totalRouteCount > 0 ? archivedCount / totalRouteCount : null;
  const phaseAbandonRate = phases.length > 0 ? skippedPhases.length / phases.length : null;
  const skipRate = phases.length > 0 ? skippedPhases.length / phases.length : null;

  // Archive-based hazards: same phase label skipped in 3+ projects
  const abandonCounts = new Map<string, number>();
  const allProjectRoutes = store.getRoutesForProject(route.project);
  for (const r of allProjectRoutes) {
    const routePhases = store.getPhases(r.id);
    for (const p of routePhases) {
      if (p.status === "skipped") {
        abandonCounts.set(p.label, (abandonCounts.get(p.label) ?? 0) + 1);
      }
    }
  }
  const structuralHazards: string[] = [];
  for (const [label, count] of abandonCounts) {
    if (count >= 3) {
      structuralHazards.push(`Phase "${label}" has been abandoned in ${count} projects — consider re-evaluating its approach`);
    }
  }

  const hazardCount = phases.filter((p) => p.hazards).length;
  const highVarianceCount = completedPhases.filter((p) => {
    if (p.actualCost === null || p.expectedCost === null || p.expectedCost === 0) return false;
    return p.actualCost / p.expectedCost > 3.0;
  }).length;
  const hazardPrecision = hazardCount > 0 ? highVarianceCount / hazardCount : null;
  const hazardRecall = completedPhases.length > 0 ? highVarianceCount / completedPhases.length : null;

  const replanTiming = archivedCount > 0 ? archivedCount / Math.max(totalRouteCount, 1) : null;

  const selfDiagnostics = {
    totalRoutes: totalRouteCount,
    archivedRoutes: archivedCount,
    archiveRate: archiveRate !== null ? Number(archiveRate.toFixed(3)) : null,
    phaseAbandonRate: phaseAbandonRate !== null ? Number(phaseAbandonRate.toFixed(3)) : null,
    replanTiming: replanTiming !== null ? Number(replanTiming.toFixed(3)) : null,
    mergeRate: null,
    reorderRate: null,
    skipRate: skipRate !== null ? Number(skipRate.toFixed(3)) : null,
    hazardPrecision: hazardPrecision !== null ? Number(hazardPrecision.toFixed(3)) : null,
    hazardRecall: hazardRecall !== null ? Number(hazardRecall.toFixed(3)) : null,
    structuralHazards,
  };

  const totalEvents = store.getCalibrationEvents().length;
  const meshSyncStatus = {
    reachable: meshReachable ?? false,
    pendingCheckpoints: unsyncedEvents.length,
    syncedCheckpoints: totalEvents - unsyncedEvents.length,
  };

  return {
    summary,
    deviations,
    accuracy,
    costLearning,
    pathLearning,
    selfDiagnostics,
    meshSyncStatus,
  };
}

function routeIdResolve(
  routeId: string | undefined,
  project: string | undefined,
  store: DataStore,
): string | StructuredError {
  if (routeId) return routeId;
  if (project) {
    const active = store.getActiveRoute(project);
    if (active) return active.id;
    const routes = store.getRoutesForProject(project);
    if (routes.length > 0) return routes[routes.length - 1].id;
    return { error: { code: "NOT_FOUND", message: `No route found for project "${project}"` } };
  }
  return { error: { code: "INVALID_ARGUMENT", message: "Must provide routeId or project" } };
}
