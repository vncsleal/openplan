import type { PlanResult, StructuredError } from "../core/domain.js";
import { plan } from "../core/planner.js";
import type { DataStore } from "../core/ports.js";

export interface PlanHandlerInput {
  goal: string;
  context?: string;
  replan?: boolean;
  project: string;
  store: DataStore;
  isPro: boolean;
}

export function handlePlan(input: PlanHandlerInput): PlanResult | StructuredError {
  try {
    if (!input.goal || input.goal.trim().length === 0) {
      return {
        error: { code: "INVALID_ARGUMENT", message: "goal is required and must be non-empty", param: "goal" },
      };
    }

    const result = plan({
      goal: input.goal.trim(),
      context: input.context,
      replan: input.replan,
      project: input.project,
      identityId: input.store.getIdentityId(),
      store: input.store,
    });

    // Strip personal bias for Free users
    if (!("error" in result) && !input.isPro) {
      result.personalBias = null;
    }

    return result;
  } catch (e) {
    const message = e instanceof Error ? e.message : "Unknown error";
    return {
      error: { code: "INTERNAL", message: `Plan failed: ${message}` },
    };
  }
}
