import type { DataStore } from "../core/ports.js";
import type { RouteState } from "../core/domain.js";
import { personalBias, accuracyByAction } from "../core/costs.js";

export interface RouteResource {
  uri: string;
  mimeType: string;
  text: string;
}

export function getRouteResource(project: string, store: DataStore): RouteResource | null {
  const route = store.getActiveRoute(project);
  if (!route) return null;

  const state = store.getRouteState(route.id);
  if (!state) return null;

  const text = formatRouteState(state);
  return {
    uri: `openplan://${project}/route`,
    mimeType: "application/json",
    text,
  };
}

export function getProfilesResource(store: DataStore): RouteResource {
  const events = store.getCalibrationEvents();
  const bias = personalBias(events);
  const accuracy = accuracyByAction(events);

  const text = JSON.stringify(
    {
      personalBias: bias,
      accuracyByAction: accuracy,
      totalSamples: events.length,
    },
    null,
    2,
  );

  return {
    uri: "openplan://profiles",
    mimeType: "application/json",
    text,
  };
}

export function getSyncStatusResource(store: DataStore, meshReachable: boolean): RouteResource {
  const unsynced = store.getUnsyncedCalibrationEvents();
  const all = store.getCalibrationEvents();

  const text = JSON.stringify(
    {
      reachable: meshReachable,
      pendingCheckpoints: unsynced.length,
      syncedCheckpoints: all.length - unsynced.length,
      buffer: Math.round((unsynced.length / Math.max(all.length, 1)) * 100),
      version: "0.1.0",
    },
    null,
    2,
  );

  return {
    uri: "openplan://sync-status",
    mimeType: "application/json",
    text,
  };
}

function formatRouteState(state: RouteState): string {
  const phases = state.phases.map((p, i) => ({
    sequence: i + 1,
    label: p.label,
    action: p.action,
    expectedCost: p.expectedCost,
    actualCost: p.actualCost,
    status: p.status,
  }));

  return JSON.stringify(
    {
      id: state.route.id,
      project: state.route.project,
      goal: state.route.goal,
      status: state.route.status,
      currentPhaseIndex: state.currentPhaseIndex,
      cumulativeExpected: state.cumulativeExpected,
      cumulativeActual: state.cumulativeActual,
      phases,
    },
    null,
    2,
  );
}
