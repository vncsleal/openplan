export interface Route {
  id: string;
  project: string;
  goal: string;
  goalTokens: string;
  status: "active" | "archived" | "completed";
  identityId: string;
  totalExpected: number | null;
  totalActual: number | null;
  createdAt: string;
  updatedAt: string;
}

export interface NewRoute {
  project: string;
  goal: string;
  goalTokens: string;
  identityId: string;
}

export type RouteStatus = Route["status"];

export interface RoutePhase {
  id: string;
  routeId: string;
  label: string;
  labelTokens: string;
  action: string;
  expectedCost: number | null;
  actualCost: number | null;
  status: "pending" | "in_progress" | "completed" | "skipped";
  sequence: number;
  hazards: string | null;
  deviation: number | null;
  createdAt: string;
}

export interface NewPhase {
  routeId: string;
  label: string;
  labelTokens: string;
  action: string;
  expectedCost: number | null;
  sequence: number;
  hazards?: string;
}

export type PhaseStatus = RoutePhase["status"];

export interface CalibrationEvent {
  id: string;
  action: string;
  phaseLabelTokens: string | null;
  expectedCost: number;
  actualCost: number;
  outcome: "completed" | "abandoned" | "modified";
  identityId: string;
  routeId: string | null;
  phaseId: string | null;
  synced: number;
  createdAt: string;
}

export interface NewCalibrationEvent {
  action: string;
  phaseLabelTokens: string | null;
  expectedCost: number;
  actualCost: number;
  outcome: CalibrationEvent["outcome"];
  identityId: string;
  routeId: string | null;
  phaseId: string | null;
}

export interface CorrectionEvent {
  id: string;
  calibrationEventId: string;
  previousActual: number;
  correctedActual: number;
  createdAt: string;
}

export interface NewCorrectionEvent {
  calibrationEventId: string;
  previousActual: number;
  correctedActual: number;
}

export interface CostBaseline {
  id: string;
  matchLevel: "exact" | "label_keyword" | "action";
  action: string;
  avgCost: number;
  ciLo: number | null;
  ciHi: number | null;
  sampleCount: number;
  createdAt: string;
}

export interface CompletedSequence {
  id: string;
  actionSequence: string;
  totalExpected: number;
  totalActual: number;
  efficiency: number;
  createdAt: string;
}

export interface PlanResult {
  id: string;
  project: string;
  goal: string;
  status: Route["status"];
  phases: PlanPhase[];
  evidence: RouteEvidence;
  personalBias: number | null;
  archivedRoutes: ArchivedRoute[];
}

export interface PlanPhase {
  label: string;
  action: string;
  expectedCost: number | null;
  ci: ConfidenceInterval | null;
}

export interface ConfidenceInterval {
  lo: number;
  hi: number;
}

export interface RouteEvidence {
  alternatives: string[];
  clusters: string[];
  hazards: string[];
}

export interface ArchivedRoute {
  id: string;
  goal: string;
  status: RouteStatus;
  totalExpected: number | null;
  totalActual: number | null;
  createdAt: string;
}

export interface CheckpointResult {
  phase: PlanPhase;
  deviation: number | null;
  deviationLabel: "under" | "over" | "on_track" | null;
  hazards: string[];
  nextPhase: PlanPhase | null;
  routeStatus: RouteStatus;
  cumulativeActual: number;
  cumulativeExpected: number;
}

export interface RouteState {
  route: Route;
  phases: RoutePhase[];
  currentPhaseIndex: number | null;
  cumulativeExpected: number;
  cumulativeActual: number;
}

export interface ReviewResult {
  summary: ReviewSummary;
  deviations: PhaseDeviation[];
  accuracy: ActionAccuracy[];
  costLearning: CostLearning[];
  pathLearning: PathLearning[];
  selfDiagnostics: SelfDiagnostics;
  meshSyncStatus: MeshSyncStatus;
}

export interface ReviewSummary {
  routeId: string;
  project: string;
  goal: string;
  status: RouteStatus;
  phaseCount: number;
  completedCount: number;
  skippedCount: number;
  totalExpected: number | null;
  totalActual: number | null;
  overallDeviation: number | null;
}

export interface PhaseDeviation {
  label: string;
  action: string;
  expectedCost: number | null;
  actualCost: number | null;
  deviation: number | null;
  deviationLabel: "under" | "over" | "on_track" | null;
  outcome: "completed" | "abandoned" | "modified";
}

export interface ActionAccuracy {
  action: string;
  sampleCount: number;
  meanDeviation: number | null;
  mape: number | null;
}

export interface CostLearning {
  matchLevel: CostBaseline["matchLevel"];
  action: string;
  avgCost: number;
  sampleCount: number;
}

export interface PathLearning {
  actionSequence: string;
  efficiency: number;
  totalExpected: number;
  totalActual: number;
}

export interface SelfDiagnostics {
  totalRoutes: number;
  archivedRoutes: number;
  archiveRate: number | null;
  phaseAbandonRate: number | null;
  skipRate: number | null;
  hazardPrecision: number | null;
  hazardRecall: number | null;
}

export interface MeshSyncStatus {
  reachable: boolean;
  pendingCheckpoints: number;
  syncedCheckpoints: number;
}

export type ErrorCode =
  | "INVALID_ARGUMENT"
  | "NOT_FOUND"
  | "NOT_INITIALIZED"
  | "CONFLICT"
  | "INTERNAL"
  | "MESH_UNREACHABLE";

export interface StructuredError {
  error: {
    code: ErrorCode;
    message: string;
    param?: string;
    retryAfter?: number;
  };
}
