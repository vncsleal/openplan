import type { CheckpointResult, RouteState, StructuredError } from "../core/domain.js";
import { createLogger } from "../core/logger.js";
import type { DataStore } from "../core/ports.js";
import { checkpoint } from "../core/tracker.js";

const log = createLogger("checkpoint");

export interface CheckpointHandlerInput {
  phase?: string;
  actualCost?: number;
  correct?: number;
  routeId?: string;
  project?: string;
  store: DataStore;
}

export function handleCheckpoint(input: CheckpointHandlerInput): CheckpointResult | RouteState | StructuredError {
  if (input.actualCost !== undefined) {
    if (input.actualCost <= 0) {
      return { error: { code: "INVALID_ARGUMENT", message: "actualCost must be positive" } };
    }
    if (input.phase !== undefined) {
      let routeId = input.routeId;
      if (!routeId && input.project) {
        const active = input.store.getActiveRoute(input.project);
        if (active) routeId = active.id;
      }
      if (routeId) {
        const phase = input.store.getPhaseByLabel(routeId, input.phase);
        if (phase && phase.expectedCost !== null && phase.expectedCost > 0) {
          const ratio = Math.abs(input.actualCost - phase.expectedCost) / phase.expectedCost;
          if (ratio > 10) {
            log.warn(
              `actualCost ${input.actualCost} is ${ratio.toFixed(1)}x expected ${phase.expectedCost} for phase "${input.phase}"`,
            );
          }
        }
      }
    }
  }
  if (input.correct !== undefined) {
    if (input.correct <= 0) {
      return { error: { code: "INVALID_ARGUMENT", message: "correct must be positive" } };
    }
  }

  try {
    const result = checkpoint({
      phase: input.phase,
      actualCost: input.actualCost,
      correct: input.correct,
      routeId: input.routeId,
      project: input.project,
      identityId: input.store.getIdentityId(),
      store: input.store,
    });
    return result;
  } catch (e) {
    const message = e instanceof Error ? e.message : "Unknown error";
    return {
      error: { code: "INTERNAL", message: `Checkpoint failed: ${message}` },
    };
  }
}
