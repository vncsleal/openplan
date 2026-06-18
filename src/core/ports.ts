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
} from "./domain.js";

export interface DataStore {
  createRoute(route: NewRoute): Route;
  getRoute(id: string): Route | null;
  getRouteByProjectAndGoal(project: string, goal: string): Route | null;
  getActiveRoute(project: string): Route | null;
  getArchivedRoutes(project: string): Route[];
  archiveRoute(id: string): void;
  completeRoute(id: string): void;
  updateRouteCosts(id: string, totalExpected: number, totalActual: number): void;
  getRoutesForProject(project: string): Route[];

  createPhase(phase: NewPhase): RoutePhase;
  getPhases(routeId: string): RoutePhase[];
  getPhaseByLabel(routeId: string, label: string): RoutePhase | null;
  updatePhaseCost(id: string, actualCost: number): void;
  setPhaseStatus(id: string, status: PhaseStatus): void;
  getLatestPhase(routeId: string): RoutePhase | null;
  getNextPendingPhase(routeId: string): RoutePhase | null;

  createCalibrationEvent(event: NewCalibrationEvent): CalibrationEvent;
  getCalibrationEvents(): CalibrationEvent[];
  getUnsyncedCalibrationEvents(): CalibrationEvent[];
  markCalibrationSynced(ids: string[]): void;
  getCalibrationEventsForRoute(routeId: string): CalibrationEvent[];

  createCorrectionEvent(event: NewCorrectionEvent): CorrectionEvent;
  getLastCalibrationForPhase(phaseId: string): CalibrationEvent | null;

  getBaselines(): CostBaseline[];
  setBaselines(baselines: CostBaseline[]): void;

  getSequences(): CompletedSequence[];
  addSequence(seq: CompletedSequence): void;

  getIdentityId(): string;
  getRouteState(routeId: string): RouteState | null;

  transaction<T>(fn: (store: DataStore) => T): T;
}

export interface Config {
  getIdentityId(): string;
  getProjectRoot(): string;
  getDataDir(): string;
  getMeshUrl(): string | null;
  getApiKey(): string | null;
  getCostProbeCommand(): string | null;
}

export interface CostProbe {
  start(): void;
  stop(): number | null;
}

export interface MeshSync {
  syncCheckpoints(events: CalibrationEvent[]): Promise<boolean>;
  fetchBaselines(): Promise<CostBaseline[]>;
  isReachable(): Promise<boolean>;
}
