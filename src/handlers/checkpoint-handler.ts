import { checkpoint } from "../core/tracker.js";
import type { DataStore } from "../core/ports.js";
import type { CheckpointResult, RouteState, StructuredError } from "../core/domain.js";

export interface CheckpointHandlerInput {
  phase?: string;
  actualCost?: number;
  correct?: number;
  routeId?: string;
  project?: string;
  store: DataStore;
}

export function handleCheckpoint(input: CheckpointHandlerInput): CheckpointResult | RouteState | StructuredError {
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
