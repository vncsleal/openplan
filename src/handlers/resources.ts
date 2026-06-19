import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { accuracyByAction, personalBias } from "../core/costs.js";
import type { RouteState } from "../core/domain.js";
import type { DataStore } from "../core/ports.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const pkg = JSON.parse(readFileSync(join(__dirname, "..", "..", "package.json"), "utf-8")) as { version: string };

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

export function getProfilesResource(store: DataStore, isPro = false): RouteResource {
  const events = store.getCalibrationEvents();
  const baselines = store.getBaselines();
  const bias = isPro ? personalBias(events, baselines) : null;
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
      version: pkg.version,
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
