import type { Db } from "../db/connection.js";
import { planProject } from "../core/planner.js";

export async function handlePlan(
  db: Db,
  args: { goal: string; context?: string; replan?: boolean; apiKey?: string },
): Promise<string> {
  const { goal, context = "", replan = false, apiKey } = args;

  if (!goal || goal.trim().length === 0) {
    return JSON.stringify({ error: true, message: "goal is required" });
  }

  try {
    const result = planProject(db, goal, context, replan, apiKey);
    return JSON.stringify(result, null, 2);
  } catch (err) {
    return JSON.stringify({ error: true, message: (err as Error).message });
  }
}
