import type { ReviewResult, StructuredError } from "../core/domain.js";
import type { DataStore } from "../core/ports.js";
import { review } from "../core/reviewer.js";

export interface ReviewHandlerInput {
  routeId?: string;
  project?: string;
  store: DataStore;
  meshReachable?: boolean;
}

export function handleReview(input: ReviewHandlerInput): ReviewResult | StructuredError {
  try {
    const result = review({
      routeId: input.routeId,
      project: input.project,
      identityId: input.store.getIdentityId(),
      store: input.store,
      meshReachable: input.meshReachable,
    });
    return result;
  } catch (e) {
    const message = e instanceof Error ? e.message : "Unknown error";
    return {
      error: { code: "INTERNAL", message: `Review failed: ${message}` },
    };
  }
}
