import { plan } from "../core/planner.js";
import type { DataStore } from "../core/ports.js";
import type { PlanResult, StructuredError } from "../core/domain.js";

export interface PlanHandlerInput {
  goal: string;
  context?: string;
  replan?: boolean;
  project: string;
  store: DataStore;
}

export function handlePlan(input: PlanHandlerInput): PlanResult | StructuredError {
  try {
    if (!input.goal || input.goal.trim().length === 0) {
      return {
        error: { code: "INVALID_ARGUMENT", message: "goal is required and must be non-empty", param: "goal" },
      };
    }

    return plan({
      goal: input.goal.trim(),
      context: input.context,
      replan: input.replan,
      project: input.project,
      identityId: input.store.getIdentityId(),
      store: input.store,
    });
  } catch (e) {
    const message = e instanceof Error ? e.message : "Unknown error";
    return {
      error: { code: "INTERNAL", message: `Plan failed: ${message}` },
    };
  }
}
