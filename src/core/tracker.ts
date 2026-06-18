import type { DataStore } from "./ports.js";
import type { CheckpointResult, PlanPhase, RouteState, RoutePhase, StructuredError } from "./domain.js";
import { calculateDeviation, deviationLabel, hazardFromPhases } from "./costs.js";

export interface CheckpointInput {
  phase?: string;
  actualCost?: number;
  correct?: number;
  routeId?: string;
  project?: string;
  identityId: string;
  store: DataStore;
}

export function checkpoint(input: CheckpointInput): CheckpointResult | RouteState | StructuredError {
  const { phase, actualCost, correct, routeId, project, identityId, store } = input;

  if (!phase) {
    if (routeId) {
      const state = store.getRouteState(routeId);
      if (!state) return { error: { code: "NOT_FOUND", message: "Route not found" } };
      return state;
    }
    if (project) {
      const route = store.getActiveRoute(project);
      if (!route) return { error: { code: "NOT_FOUND", message: `No active route for project "${project}"` } };
      const state = store.getRouteState(route.id);
      if (!state) return { error: { code: "NOT_FOUND", message: "Route state not found" } };
      return state;
    }
    return { error: { code: "INVALID_ARGUMENT", message: "routeId or project required for status check" } };
  }

  const resolved = routeIdResolve(routeId, project, store);
  if (typeof resolved === "object" && "error" in resolved) return resolved;

  const route = store.getRoute(resolved);
  if (!route) return { error: { code: "NOT_FOUND", message: "Route not found" } };

  const phases = store.getPhases(resolved);
  const matchedPhase = findPhaseBySubsumption(phases, phase);
  if (!matchedPhase) return { error: { code: "NOT_FOUND", message: `Phase "${phase}" not found` } };

  if (correct !== undefined) {
    store.updatePhaseCost(matchedPhase.id, correct);
    const lastCalibration = store.getLastCalibrationForPhase(matchedPhase.id);
    if (lastCalibration) {
      store.createCorrectionEvent({
        calibrationEventId: lastCalibration.id,
        previousActual: lastCalibration.actualCost,
        correctedActual: correct,
      });
    }

    const allPhases = store.getPhases(resolved);
    const totalActualValue = allPhases.reduce((s, p) => s + (p.actualCost ?? 0), 0);
    const totalExpectedValue = allPhases.reduce((s, p) => s + (p.expectedCost ?? 0), 0);
    store.updateRouteCosts(resolved, totalExpectedValue, totalActualValue);

    const updatedPhases = store.getPhases(resolved);
    const updatedPhase = updatedPhases.find((p) => p.id === matchedPhase.id);
    if (!updatedPhase) return { error: { code: "INTERNAL", message: "Phase not found after update" } };
    return buildCheckpointResult(updatedPhases, updatedPhase);
  }

  if (actualCost !== undefined) {
    const previousActual = matchedPhase.actualCost;
    const newActual = previousActual !== null ? previousActual + actualCost : actualCost;
    store.updatePhaseCost(matchedPhase.id, newActual);
    store.setPhaseStatus(matchedPhase.id, "completed");

    store.createCalibrationEvent({
      action: matchedPhase.action,
      phaseLabelTokens: matchedPhase.labelTokens,
      expectedCost: matchedPhase.expectedCost ?? 0,
      actualCost: newActual,
      outcome: "completed",
      identityId,
      routeId: resolved,
      phaseId: matchedPhase.id,
    });

    const allPhases = store.getPhases(resolved);
    const totalActualValue = allPhases.reduce((s, p) => s + (p.actualCost ?? 0), 0);
    const totalExpectedValue = allPhases.reduce((s, p) => s + (p.expectedCost ?? 0), 0);
    store.updateRouteCosts(resolved, totalExpectedValue, totalActualValue);

    const completedCount = allPhases.filter((p) => p.status === "completed").length;
    if (completedCount === allPhases.length) {
      store.completeRoute(resolved);
    }

    const updatedPhases = store.getPhases(resolved);
    const updatedPhase = updatedPhases.find((p) => p.id === matchedPhase.id);
    if (!updatedPhase) return { error: { code: "INTERNAL", message: "Phase not found after update" } };
    return buildCheckpointResult(updatedPhases, updatedPhase);
  }

  const updatedPhases = store.getPhases(resolved);
  const currentPhase = updatedPhases.find((p) => p.id === matchedPhase.id);
  if (!currentPhase) return { error: { code: "INTERNAL", message: "Phase not found after update" } };
  return buildCheckpointResult(updatedPhases, currentPhase);
}

function routeIdResolve(
  routeId: string | undefined,
  project: string | undefined,
  store: DataStore | undefined,
): string | StructuredError {
  if (routeId) return routeId;
  if (project && store) {
    const route = store.getActiveRoute(project);
    if (route) return route.id;
    return { error: { code: "NOT_FOUND", message: `No active route for project "${project}"` } };
  }
  return { error: { code: "INVALID_ARGUMENT", message: "Must provide routeId or project" } };
}

function findPhaseBySubsumption(phases: RoutePhase[], labelFilter: string): RoutePhase | null {
  const lower = labelFilter.toLowerCase();
  const exact = phases.find((p) => p.label.toLowerCase() === lower);
  if (exact) return exact;

  const subsumed = phases.find((p) => p.label.toLowerCase().includes(lower));
  if (subsumed) return subsumed;

  return null;
}

function buildCheckpointResult(phases: RoutePhase[], currentPhase: RoutePhase): CheckpointResult {
  const completed = phases.filter((p) => p.status === "completed");
  const totalActual = completed.reduce((s, p) => s + (p.actualCost ?? 0), 0);
  const totalExpected = completed.reduce((s, p) => s + (p.expectedCost ?? 0), 0);
  const deviation =
    currentPhase.actualCost !== null && currentPhase.expectedCost !== null
      ? calculateDeviation(currentPhase.actualCost, currentPhase.expectedCost)
      : null;
  const label = deviationLabel(deviation, deviation !== null ? currentPhase.expectedCost : null);

  const sortedBySeq = [...phases].sort((a, b) => a.sequence - b.sequence);
  const nextPhase = sortedBySeq.find((p) => p.status === "pending" && p.sequence > currentPhase.sequence);

  const allComplete = phases.every((p) => p.status === "completed" || p.status === "skipped");

  const planPhase: PlanPhase = {
    label: currentPhase.label,
    action: currentPhase.action,
    expectedCost: currentPhase.expectedCost,
    ci: null,
  };

  const nextPlanPhase: PlanPhase | null = nextPhase
    ? { label: nextPhase.label, action: nextPhase.action, expectedCost: nextPhase.expectedCost, ci: null }
    : null;

  return {
    phase: planPhase,
    deviation,
    deviationLabel: label,
    hazards: hazardFromPhases(phases),
    nextPhase: nextPlanPhase,
    routeStatus: allComplete ? "completed" : "active",
    cumulativeActual: totalActual,
    cumulativeExpected: totalExpected,
  };
}
