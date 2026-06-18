import type { DataStore } from "./ports.js";
import type { PlanPhase, PlanResult, ArchivedRoute } from "./domain.js";
import { tokenize, matchLevel } from "./tokenizer.js";
import { ciFromBaseline, personalBias } from "./costs.js";
export interface PlanInput {
  goal: string;
  context?: string;
  replan?: boolean;
  project: string;
  identityId: string;
  store: DataStore;
}

const DEFAULT_ACTIONS = [
  "implement",
  "test",
  "refactor",
  "document",
  "configure",
  "review",
  "research",
  "debug",
  "optimize",
  "migrate",
];

function estimateAction(action: string): number {
  const weights: Record<string, number> = {
    implement: 600,
    test: 300,
    refactor: 400,
    document: 200,
    configure: 150,
    review: 200,
    research: 450,
    debug: 500,
    optimize: 350,
    migrate: 800,
  };
  return weights[action] ?? 400;
}

function decompose(goal: string, context?: string): { label: string; action: string }[] {
  const phases: { label: string; action: string }[] = [];
  const lower = goal.toLowerCase();

  if (lower.includes("implement") || lower.includes("create") || lower.includes("build") || lower.includes("add")) {
    const basePhases = [
      { label: "Research and Planning", action: "research" },
      { label: "Implementation", action: "implement" },
      { label: "Testing", action: "test" },
      { label: "Documentation", action: "document" },
    ];
    phases.push(...basePhases);
  } else if (lower.includes("refactor") || lower.includes("restructure") || lower.includes("improve")) {
    phases.push(
      { label: "Analysis", action: "research" },
      { label: "Refactoring", action: "refactor" },
      { label: "Verification", action: "test" },
    );
  } else if (lower.includes("fix") || lower.includes("bug") || lower.includes("debug")) {
    phases.push(
      { label: "Diagnosis", action: "debug" },
      { label: "Fix", action: "implement" },
      { label: "Verification", action: "test" },
    );
  } else if (lower.includes("config") || lower.includes("setup") || lower.includes("deploy")) {
    phases.push(
      { label: "Configuration", action: "configure" },
      { label: "Validation", action: "test" },
      { label: "Documentation", action: "document" },
    );
  } else if (lower.includes("migrate") || lower.includes("upgrade") || lower.includes("migration")) {
    phases.push(
      { label: "Migration Planning", action: "research" },
      { label: "Migration", action: "migrate" },
      { label: "Validation", action: "test" },
    );
  } else {
    phases.push(
      { label: "Planning", action: "research" },
      { label: "Implementation", action: "implement" },
      { label: "Review", action: "review" },
    );
  }

  if (context) {
    const ctx = context.toLowerCase();
    if (ctx.includes("api") || ctx.includes("backend")) {
      phases.unshift({ label: "API Design", action: "research" });
    }
    if (ctx.includes("ui") || ctx.includes("frontend") || ctx.includes("component")) {
      phases.unshift({ label: "UI Design", action: "research" });
    }
  }

  const seen = new Set<string>();
  return phases.filter((p) => {
    const key = p.label.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function plan(input: PlanInput): PlanResult {
  const { goal, replan, project, identityId, store } = input;

  if (replan) {
    const existing = store.getActiveRoute(project);
    if (existing) {
      store.archiveRoute(existing.id);
    }
  } else {
    const existing = store.getRouteByProjectAndGoal(project, goal);
    if (existing) {
      const phases = store.getPhases(existing.id);
      return buildPlanResult(existing, phases, store, identityId, project);
    }
  }

  const goalTokens = tokenize(goal);
  const phases = decompose(goal, input.context);

  const route = store.transaction(() => {
    const r = store.createRoute({
      project,
      goal,
      goalTokens,
      identityId,
    });

    const baselines = store.getBaselines();
    const bias = personalBias(store.getCalibrationEvents());

    let totalExpected = 0;

    phases.forEach((p, i) => {
      const labelTokens = tokenize(p.label);
      const cost = estimateAction(p.action);
      const costInfo = ciFromBaseline(baselines, goalTokens, labelTokens, p.action);
      const expectedCost = costInfo?.expected ?? cost;
      const adjustedCost = bias !== null ? Math.round(expectedCost * bias) : expectedCost;

      totalExpected += adjustedCost;

      store.createPhase({
        routeId: r.id,
        label: p.label,
        labelTokens,
        action: p.action,
        expectedCost: adjustedCost,
        sequence: i,
      });
    });

    store.updateRouteCosts(r.id, totalExpected, 0);
    return r;
  });

  const storedPhases = store.getPhases(route.id);
  return buildPlanResult(route, storedPhases, store, identityId, project);
}

function buildPlanResult(
  route: {
    id: string;
    project: string;
    goal: string;
    status: "active" | "archived" | "completed";
    totalExpected: number | null;
    totalActual: number | null;
    createdAt: string;
  },
  phases: { label: string; action: string; expectedCost: number | null; status: string; hazards: string | null }[],
  store: DataStore,
  identityId: string,
  project: string,
): PlanResult {
  const baselines = store.getBaselines();
  const events = store.getCalibrationEvents();
  const bias = personalBias(events);

  const planPhases = phases.map((p) => {
    const baseline = ciFromBaseline(baselines, tokenize(route.goal), tokenize(p.label), p.action);
    return {
      label: p.label,
      action: p.action,
      expectedCost: p.expectedCost,
      ci: baseline?.ci ?? null,
    };
  });

  const archivedRoutes = store.getArchivedRoutes(project).map((r) => ({
    id: r.id,
    goal: r.goal,
    status: r.status,
    totalExpected: r.totalExpected,
    totalActual: r.totalActual,
    createdAt: r.createdAt,
  }));

  return {
    id: route.id,
    project: route.project,
    goal: route.goal,
    status: route.status,
    phases: planPhases,
    evidence: {
      alternatives: [],
      clusters: [],
      hazards: phases
        .filter((p): p is typeof p & { hazards: string } => p.hazards !== null)
        .map((p) => p.hazards)
        .flatMap((h) => h.split("\n")),
    },
    personalBias: bias,
    archivedRoutes,
  };
}
