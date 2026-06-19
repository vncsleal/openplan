import { eq, and, desc } from "drizzle-orm";
import type { BetterSQLite3Database } from "drizzle-orm/better-sqlite3";
import { randomUUID } from "node:crypto";
import * as schema from "./schema.js";
import type { DataStore } from "../core/ports.js";
import type {
  Route,
  NewRoute,
  RoutePhase,
  NewPhase,
  CalibrationEvent,
  NewCalibrationEvent,
  CorrectionEvent,
  NewCorrectionEvent,
  CostBaseline,
  CompletedSequence,
  RouteState,
  PhaseStatus,
} from "../core/domain.js";

export function createStore(database: BetterSQLite3Database<typeof schema>, identityId: string): DataStore {
  const db = database;
  function toRoute(r: typeof schema.routes.$inferSelect): Route {
    return {
      id: r.id,
      project: r.project,
      goal: r.goal,
      goalTokens: r.goalTokens,
      status: r.status,
      identityId: r.identityId,
      totalExpected: r.totalExpected,
      totalActual: r.totalActual,
      createdAt: r.createdAt,
      updatedAt: r.updatedAt,
    };
  }

  function toPhase(p: typeof schema.routePhases.$inferSelect): RoutePhase {
    return {
      id: p.id,
      routeId: p.routeId,
      label: p.label,
      labelTokens: p.labelTokens,
      action: p.action,
      expectedCost: p.expectedCost,
      actualCost: p.actualCost,
      status: p.status,
      sequence: p.sequence,
      hazards: p.hazards,
      deviation: p.deviation,
      createdAt: p.createdAt,
    };
  }

  function toCalibrationEvent(e: typeof schema.calibrationEvents.$inferSelect): CalibrationEvent {
    return {
      id: e.id,
      action: e.action,
      phaseLabelTokens: e.phaseLabelTokens,
      expectedCost: e.expectedCost,
      actualCost: e.actualCost,
      outcome: e.outcome,
      identityId: e.identityId,
      projectType: e.projectType,
      routeId: e.routeId,
      phaseId: e.phaseId,
      synced: e.synced,
      createdAt: e.createdAt,
    };
  }

  function toCostBaseline(b: typeof schema.costBaselines.$inferSelect): CostBaseline {
    return {
      id: b.id,
      matchLevel: b.matchLevel,
      action: b.action,
      avgCost: b.avgCost,
      ciLo: b.ciLo,
      ciHi: b.ciHi,
      sampleCount: b.sampleCount,
      createdAt: b.createdAt,
    };
  }

  function toCompletedSequence(s: typeof schema.completedSequences.$inferSelect): CompletedSequence {
    return {
      id: s.id,
      actionSequence: s.actionSequence,
      totalExpected: s.totalExpected,
      totalActual: s.totalActual,
      efficiency: s.efficiency,
      createdAt: s.createdAt,
    };
  }

  return {
    createRoute(newRoute: NewRoute): Route {
      const now = new Date().toISOString();
      const id = randomUUID();
      db.insert(schema.routes)
        .values({
          id,
          project: newRoute.project,
          goal: newRoute.goal,
          goalTokens: newRoute.goalTokens,
          status: "active",
          identityId: newRoute.identityId,
          totalExpected: null,
          totalActual: null,
          createdAt: now,
          updatedAt: now,
        })
        .run();
      const row = db.select().from(schema.routes).where(eq(schema.routes.id, id)).get();
      if (!row) throw new Error("Failed to create route");
      return toRoute(row);
    },

    getRoute(id: string): Route | null {
      const row = db.select().from(schema.routes).where(eq(schema.routes.id, id)).get();
      return row ? toRoute(row) : null;
    },

    getRouteByProjectAndGoal(project: string, goal: string): Route | null {
      const row = db
        .select()
        .from(schema.routes)
        .where(
          and(eq(schema.routes.project, project), eq(schema.routes.goal, goal), eq(schema.routes.status, "active")),
        )
        .get();
      return row ? toRoute(row) : null;
    },

    getActiveRoute(project: string): Route | null {
      const row = db
        .select()
        .from(schema.routes)
        .where(and(eq(schema.routes.project, project), eq(schema.routes.status, "active")))
        .get();
      return row ? toRoute(row) : null;
    },

    getArchivedRoutes(project: string): Route[] {
      return db
        .select()
        .from(schema.routes)
        .where(and(eq(schema.routes.project, project), eq(schema.routes.status, "archived")))
        .all()
        .map(toRoute);
    },

    archiveRoute(id: string): void {
      const now = new Date().toISOString();
      db.update(schema.routes).set({ status: "archived", updatedAt: now }).where(eq(schema.routes.id, id)).run();
    },

    completeRoute(id: string): void {
      const now = new Date().toISOString();
      db.update(schema.routes).set({ status: "completed", updatedAt: now }).where(eq(schema.routes.id, id)).run();
    },

    updateRouteCosts(id: string, totalExpected: number, totalActual: number): void {
      const now = new Date().toISOString();
      db.update(schema.routes)
        .set({ totalExpected, totalActual, updatedAt: now })
        .where(eq(schema.routes.id, id))
        .run();
    },

    getRoutesForProject(project: string): Route[] {
      return db.select().from(schema.routes).where(eq(schema.routes.project, project)).all().map(toRoute);
    },

    createPhase(newPhase: NewPhase): RoutePhase {
      const now = new Date().toISOString();
      const id = randomUUID();
      const phase: typeof schema.routePhases.$inferInsert = {
        id,
        routeId: newPhase.routeId,
        label: newPhase.label,
        labelTokens: newPhase.labelTokens,
        action: newPhase.action,
        expectedCost: newPhase.expectedCost,
        actualCost: null,
        status: "pending",
        sequence: newPhase.sequence,
        hazards: newPhase.hazards ?? null,
        deviation: null,
        createdAt: now,
      };
      db.insert(schema.routePhases).values(phase).run();
      const row = db.select().from(schema.routePhases).where(eq(schema.routePhases.id, id)).get();
      if (!row) throw new Error("Failed to create phase");
      return toPhase(row);
    },

    getPhases(routeId: string): RoutePhase[] {
      return db
        .select()
        .from(schema.routePhases)
        .where(eq(schema.routePhases.routeId, routeId))
        .orderBy(schema.routePhases.sequence)
        .all()
        .map(toPhase);
    },

    getPhaseByLabel(routeId: string, label: string): RoutePhase | null {
      const row = db
        .select()
        .from(schema.routePhases)
        .where(and(eq(schema.routePhases.routeId, routeId), eq(schema.routePhases.label, label)))
        .get();
      return row ? toPhase(row) : null;
    },

    updatePhaseCost(id: string, actualCost: number): void {
      db.update(schema.routePhases).set({ actualCost }).where(eq(schema.routePhases.id, id)).run();
    },

    setPhaseStatus(id: string, status: PhaseStatus): void {
      db.update(schema.routePhases).set({ status }).where(eq(schema.routePhases.id, id)).run();
    },

    getLatestPhase(routeId: string): RoutePhase | null {
      const row = db
        .select()
        .from(schema.routePhases)
        .where(eq(schema.routePhases.routeId, routeId))
        .orderBy(desc(schema.routePhases.sequence))
        .get();
      return row ? toPhase(row) : null;
    },

    getNextPendingPhase(routeId: string): RoutePhase | null {
      const row = db
        .select()
        .from(schema.routePhases)
        .where(and(eq(schema.routePhases.routeId, routeId), eq(schema.routePhases.status, "pending")))
        .orderBy(schema.routePhases.sequence)
        .get();
      return row ? toPhase(row) : null;
    },

    createCalibrationEvent(event: NewCalibrationEvent): CalibrationEvent {
      const now = new Date().toISOString();
      const id = randomUUID();
      db.insert(schema.calibrationEvents)
        .values({
          id,
          action: event.action,
          phaseLabelTokens: event.phaseLabelTokens,
          expectedCost: event.expectedCost,
          actualCost: event.actualCost,
          outcome: event.outcome,
          identityId: event.identityId,
          projectType: event.projectType,
          routeId: event.routeId,
          phaseId: event.phaseId,
          synced: 0,
          createdAt: now,
        })
        .run();
      const row = db.select().from(schema.calibrationEvents).where(eq(schema.calibrationEvents.id, id)).get();
      if (!row) throw new Error("Failed to create calibration event");
      return toCalibrationEvent(row);
    },

    getCalibrationEvents(): CalibrationEvent[] {
      return db.select().from(schema.calibrationEvents).all().map(toCalibrationEvent);
    },

    getUnsyncedCalibrationEvents(): CalibrationEvent[] {
      return db
        .select()
        .from(schema.calibrationEvents)
        .where(eq(schema.calibrationEvents.synced, 0))
        .all()
        .map(toCalibrationEvent);
    },

    markCalibrationSynced(ids: string[]): void {
      for (const id of ids) {
        db.update(schema.calibrationEvents).set({ synced: 1 }).where(eq(schema.calibrationEvents.id, id)).run();
      }
    },

    getCalibrationEventsForRoute(routeId: string): CalibrationEvent[] {
      return db
        .select()
        .from(schema.calibrationEvents)
        .where(eq(schema.calibrationEvents.routeId, routeId))
        .all()
        .map(toCalibrationEvent);
    },

    createCorrectionEvent(event: NewCorrectionEvent): CorrectionEvent {
      const now = new Date().toISOString();
      const id = randomUUID();
      db.insert(schema.correctionEvents)
        .values({
          id,
          calibrationEventId: event.calibrationEventId,
          previousActual: event.previousActual,
          correctedActual: event.correctedActual,
          createdAt: now,
        })
        .run();
      const row = db.select().from(schema.correctionEvents).where(eq(schema.correctionEvents.id, id)).get();
      if (!row) throw new Error("Failed to create correction event");
      return {
        id: row.id,
        calibrationEventId: row.calibrationEventId,
        previousActual: row.previousActual,
        correctedActual: row.correctedActual,
        createdAt: row.createdAt,
      };
    },

    getLastCalibrationForPhase(phaseId: string): CalibrationEvent | null {
      const row = db
        .select()
        .from(schema.calibrationEvents)
        .where(eq(schema.calibrationEvents.phaseId, phaseId))
        .orderBy(desc(schema.calibrationEvents.createdAt))
        .get();
      return row ? toCalibrationEvent(row) : null;
    },

    getBaselines(): CostBaseline[] {
      return db.select().from(schema.costBaselines).all().map(toCostBaseline);
    },

    setBaselines(baselines: CostBaseline[]): void {
      db.delete(schema.costBaselines).run();
      const now = new Date().toISOString();
      for (const b of baselines) {
        db.insert(schema.costBaselines)
          .values({
            id: b.id,
            matchLevel: b.matchLevel,
            action: b.action,
            avgCost: b.avgCost,
            ciLo: b.ciLo,
            ciHi: b.ciHi,
            sampleCount: b.sampleCount,
            createdAt: now,
          })
          .run();
      }
    },

    getSequences(): CompletedSequence[] {
      return db.select().from(schema.completedSequences).all().map(toCompletedSequence);
    },

    addSequence(seq: CompletedSequence): void {
      db.insert(schema.completedSequences)
        .values({
          id: seq.id,
          actionSequence: seq.actionSequence,
          totalExpected: seq.totalExpected,
          totalActual: seq.totalActual,
          efficiency: seq.efficiency,
          createdAt: seq.createdAt,
        })
        .run();
    },

    getIdentityId(): string {
      return identityId;
    },

    getRouteState(routeId: string): RouteState | null {
      const route = this.getRoute(routeId);
      if (!route) return null;
      const phases = this.getPhases(routeId);
      const lastCompleted = [...phases].reverse().find((p) => p.status === "completed");
      const currentPhaseIndex = lastCompleted ? phases.findIndex((p) => p.id === lastCompleted.id) + 1 : 0;
      const totalExpected = phases.reduce((s, p) => s + (p.expectedCost ?? 0), 0);
      const totalActual = phases.reduce((s, p) => s + (p.actualCost ?? 0), 0);

      return {
        route,
        phases,
        currentPhaseIndex: currentPhaseIndex < phases.length ? currentPhaseIndex : null,
        cumulativeExpected: totalExpected,
        cumulativeActual: totalActual,
      };
    },

    transaction<T>(fn: (store: DataStore) => T): T {
      return db.transaction(() => fn(this));
    },
  };
}
