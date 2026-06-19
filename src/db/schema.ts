import { sqliteTable, text, integer, real } from "drizzle-orm/sqlite-core";

const routeStatus = ["active", "archived", "completed"] as const;
type RouteStatus = (typeof routeStatus)[number];

const phaseStatus = ["pending", "in_progress", "completed", "skipped"] as const;
type PhaseStatus = (typeof phaseStatus)[number];

const outcomeType = ["completed", "abandoned", "modified"] as const;
type OutcomeType = (typeof outcomeType)[number];

const matchLevelType = ["exact", "label_keyword", "action"] as const;
type MatchLevelType = (typeof matchLevelType)[number];

export const routes = sqliteTable("routes", {
  id: text("id").primaryKey(),
  project: text("project").notNull(),
  goal: text("goal").notNull(),
  goalTokens: text("goal_tokens").notNull(),
  status: text("status").$type<RouteStatus>().notNull().default("active"),
  identityId: text("identity_id").notNull(),
  totalExpected: real("total_expected"),
  totalActual: real("total_actual"),
  createdAt: text("created_at").notNull(),
  updatedAt: text("updated_at").notNull(),
});

export const routePhases = sqliteTable("route_phases", {
  id: text("id").primaryKey(),
  routeId: text("route_id")
    .notNull()
    .references(() => routes.id),
  label: text("label").notNull(),
  labelTokens: text("label_tokens").notNull(),
  action: text("action").notNull(),
  expectedCost: real("expected_cost"),
  actualCost: real("actual_cost"),
  status: text("status").$type<PhaseStatus>().notNull().default("pending"),
  sequence: integer("sequence").notNull(),
  hazards: text("hazards"),
  deviation: real("deviation"),
  createdAt: text("created_at").notNull(),
});

export const calibrationEvents = sqliteTable("calibration_events", {
  id: text("id").primaryKey(),
  action: text("action").notNull(),
  phaseLabelTokens: text("phase_label_tokens"),
  expectedCost: real("expected_cost").notNull(),
  actualCost: real("actual_cost").notNull(),
  outcome: text("outcome").$type<OutcomeType>().notNull(),
  identityId: text("identity_id").notNull(),
  projectType: text("project_type").notNull().default("software"),
  routeId: text("route_id"),
  phaseId: text("phase_id"),
  synced: integer("synced").notNull().default(0),
  createdAt: text("created_at").notNull(),
});

export const correctionEvents = sqliteTable("correction_events", {
  id: text("id").primaryKey(),
  calibrationEventId: text("calibration_event_id")
    .notNull()
    .references(() => calibrationEvents.id),
  previousActual: real("previous_actual").notNull(),
  correctedActual: real("corrected_actual").notNull(),
  createdAt: text("created_at").notNull(),
});

export const costBaselines = sqliteTable("cost_baselines", {
  id: text("id").primaryKey(),
  matchLevel: text("match_level").$type<MatchLevelType>().notNull(),
  action: text("action").notNull(),
  avgCost: real("avg_cost").notNull(),
  ciLo: real("ci_lo"),
  ciHi: real("ci_hi"),
  sampleCount: integer("sample_count").notNull(),
  createdAt: text("created_at").notNull(),
});

export const completedSequences = sqliteTable("completed_sequences", {
  id: text("id").primaryKey(),
  actionSequence: text("action_sequence").notNull(),
  totalExpected: real("total_expected").notNull(),
  totalActual: real("total_actual").notNull(),
  efficiency: real("efficiency").notNull(),
  createdAt: text("created_at").notNull(),
});

export const schemaVersion = sqliteTable("schema_version", {
  version: integer("version").notNull(),
});
