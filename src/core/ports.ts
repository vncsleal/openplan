export interface CostProbe {
  start(): Promise<void>;
  stop(): Promise<number | null>;
}

export interface MeshPort {
  pushCheckpoints(checkpoints: CalibrationEvent[]): Promise<boolean>;
  pullBaselines(): Promise<MeshBaseline[]>;
  syncPending(events: CalibrationEvent[]): Promise<{ synced: number; pending: number }>;
}

export interface CalibrationEvent {
  action: string;
  phaseLabelTokens: string;
  expectedCost: number;
  actualCost: number;
  outcome: string;
  apiKey?: string;
}

export interface MeshBaseline {
  matchLevel: string;
  action: string;
  phaseLabelTokens: string;
  avgCost: number;
  ciLo: number;
  ciHi: number;
  sampleCount: number;
  successRate: number;
}

export interface Phase {
  label: string;
  action: string;
  expectedCost: number;
  ci: [number, number];
  matchLevel?: string;
  matchSamples?: number;
}

export interface Route {
  id: string;
  project: string;
  goal: string;
  totalExpected: number;
  totalActual: number;
  status: string;
  archived: boolean;
  abandonReason?: string;
  completedAt?: string;
  goalTokens: string;
  contextTokens: string;
  createdAt: string;
}

export interface RoutePhase {
  id: string;
  routeId: string;
  label: string;
  action: string;
  expectedCost: number;
  actualCost?: number;
  outcome?: string;
  status: string;
  sequence: number;
  labelTokens: string;
  createdAt: string;
}

export interface CheckpointResult {
  phaseCompleted: string;
  actualCost: number;
  expectedCost: number;
  deviation: { ratio: number; level: string; outcome: string };
  nextPhase: { label: string; expectedCost: number; ci: [number, number] } | null;
  hazards: Hazard[];
  routeCompleted: boolean;
}

export interface Hazard {
  type: string;
  detail: string;
  suggestedBuffer?: number;
}

export interface ReviewResult {
  summary: {
    estimated: number;
    actual: number;
    phasesCompleted: number;
    accuracy: number;
  };
  deviations: Array<{
    phase: string;
    expected: number;
    actual: number;
    ratio: number;
  }>;
  accuracyByAction: Record<string, { count: number; avgDeviation: number }>;
  costLearning: Array<{ action: string; avgCost: number; samples: number }>;
  pathLearning: Array<{ sequence: string; efficiency: number; samples: number }>;
  selfDiagnostics: Record<string, unknown>;
  mesh: { shared: number; pending: number };
}
