import type Database from "better-sqlite3";
import { reviewRoute } from "../core/reviewer.js";

export async function handleReview(
  sqlite: Database.Database,
  args: { routeId?: string | null; project?: string | null },
): Promise<string> {
  const { routeId, project } = args;

  try {
    const result = reviewRoute(sqlite, routeId ?? undefined, project ?? undefined);
    return JSON.stringify(result, null, 2);
  } catch (err) {
    return JSON.stringify({ error: true, message: (err as Error).message });
  }
}
