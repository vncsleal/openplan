import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Database from "better-sqlite3";
import { tokenize, deriveOutcome, estimateCost } from "../src/core/costs.js";
import { planProject } from "../src/core/planner.js";
import { checkpointPhase, getRouteStatus } from "../src/core/tracker.js";
import { reviewRoute } from "../src/core/reviewer.js";
import { initTables, seedDefaults } from "../src/db/connection.js";
import type DatabaseType from "better-sqlite3";

let db: DatabaseType.Database;

beforeEach(() => {
  db = new Database(":memory:");
  db.pragma("journal_mode = WAL");
  db.pragma("foreign_keys = ON");
  initTables(db);
  seedDefaults(db);
});

afterEach(() => {
  db.close();
});

describe("tokenize", () => {
  it("removes stop words and short tokens", () => {
    expect(tokenize("the build a website for testing")).toBe("build website testing");
  });

  it("removes special characters", () => {
    expect(tokenize("Hello, World! It's a test.")).toBe("hello world test");
  });

  it("returns empty string for stop words only", () => {
    expect(tokenize("the a an is it")).toBe("");
  });
});

describe("deriveOutcome", () => {
  it("returns success when ratio <= 1.3", () => {
    expect(deriveOutcome(1000, 1200)).toBe("success");
  });

  it("returns partial when ratio <= 2.0", () => {
    expect(deriveOutcome(1000, 1800)).toBe("partial");
  });

  it("returns failure when ratio > 2.0", () => {
    expect(deriveOutcome(1000, 2500)).toBe("failure");
  });

  it("handles zero expected cost", () => {
    expect(deriveOutcome(0, 100)).toBe("success");
  });
});

describe("planProject", () => {
  it("creates a route with phases", () => {
    const result = planProject(db, "Build a newsletter platform", "Next.js");
    expect(result.route).toBeDefined();
    expect(result.route.phases.length).toBeGreaterThan(0);
    expect(result.route.id).toMatch(/^R-/);
  });

  it("is idempotent for same goal", () => {
    const r1 = planProject(db, "Build a test app");
    const r2 = planProject(db, "Build a test app");
    expect(r2.route.id).toBe(r1.route.id);
  });

  it("replan archives old route and creates new", () => {
    const r1 = planProject(db, "Build a test app");
    const r2 = planProject(db, "Build a test app", "", true);
    expect(r2.route.id).not.toBe(r1.route.id);
  });
});

describe("checkpointPhase", () => {
  it("records phase completion and returns next phase", () => {
    const plan = planProject(db, "Build a test app");
    const routeId = plan.route.id;
    const firstPhase = plan.route.phases[0];

    const result = checkpointPhase(db, routeId, firstPhase.label, 1500);
    expect(result.phaseCompleted).toBe(firstPhase.label);
    expect(result.actualCost).toBe(1500);
    expect(result.routeCompleted).toBe(false);
    expect(result.nextPhase).not.toBeNull();
  });

  it("marks route completed on last phase", () => {
    const plan = planProject(db, "Small project");
    const routeId = plan.route.id;
    const phases = plan.route.phases;

    for (const phase of phases) {
      checkpointPhase(db, routeId, phase.label, 1000);
    }

    const status = getRouteStatus(db, routeId);
    expect(status.status).toBe("completed");
  });

  it("returns status mode with no args", () => {
    const plan = planProject(db, "Status test");
    const routeId = plan.route.id;
    checkpointPhase(db, routeId, plan.route.phases[0].label, 500);

    const status = db.prepare(
      "SELECT id FROM routes WHERE archived = 0 AND status = 'active' ORDER BY created_at DESC LIMIT 1"
    ).get() as { id: string };
    const routeStatus = getRouteStatus(db, status.id);
    expect(routeStatus.phases).toBeDefined();
    expect((routeStatus.phases as unknown[]).length).toBeGreaterThan(0);
  });
});

describe("reviewRoute", () => {
  it("returns summary for completed route", () => {
    const plan = planProject(db, "Review test");
    const routeId = plan.route.id;

    for (const phase of plan.route.phases) {
      checkpointPhase(db, routeId, phase.label, 1000);
    }

    const review = reviewRoute(db, routeId);
    if ("error" in review) throw new Error(review.error);
    expect(review.summary.phasesCompleted).toBe(plan.route.phases.length);
    expect(review.summary.actual).toBeGreaterThan(0);
  });

  it("returns review for partially complete route", () => {
    const plan = planProject(db, "Incomplete");
    const routeId = plan.route.id;
    checkpointPhase(db, routeId, plan.route.phases[0].label, 500);

    const review = reviewRoute(db, routeId);
    expect("summary" in review).toBe(true);
  });
});
