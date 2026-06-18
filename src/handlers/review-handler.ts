import type { Db } from "../db/connection.js";
import { reviewRoute } from "../core/reviewer.js";

export async function handleReview(
  db: Db,
  args: { routeId?: string | null; project?: string | null },
): Promise<string> {
  const { routeId, project } = args;

  try {
    const result = reviewRoute(db, routeId ?? undefined, project ?? undefined);
    return JSON.stringify(result, null, 2);
  } catch (err) {
    return JSON.stringify({ error: true, message: (err as Error).message });
  }
}
