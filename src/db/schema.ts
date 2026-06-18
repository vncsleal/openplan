import { sqliteTable, text, integer, real } from "drizzle-orm/sqlite-core";

export const routes = sqliteTable("routes", {
  id: text("id").primaryKey(),
  project: text("project").notNull(),
  goal: text("goal").notNull(),
  context: text("context").default(""),
  totalExpected: real("total_expected").notNull(),
  totalActual: real("total_actual"),
  status: text("status").notNull().default("active"),
  archived: integer("archived", { mode: "boolean" }).notNull().default(false),
  abandonReason: text("abandon_reason"),
  completedAt: text("completed_at"),
  goalTokens: text("goal_tokens").default(""),
  contextTokens: text("context_tokens").default(""),
  createdAt: text("created_at").notNull(),
});

export const routePhases = sqliteTable("route_phases", {
  id: text("id").primaryKey(),
  routeId: text("route_id").notNull().references(() => routes.id),
  label: text("label").notNull(),
  action: text("action").notNull(),
  expectedCost: real("expected_cost").notNull(),
  actualCost: real("actual_cost"),
  outcome: text("outcome"),
  status: text("status").notNull().default("pending"),
  sequence: integer("sequence").notNull(),
  labelTokens: text("label_tokens").default(""),
  createdAt: text("created_at").notNull(),
});

export const calibrationEvents = sqliteTable("calibration_events", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  action: text("action").notNull(),
  phaseLabelTokens: text("phase_label_tokens").notNull(),
  expectedCost: real("expected_cost").notNull(),
  actualCost: real("actual_cost").notNull(),
  outcome: text("outcome").notNull(),
  apiKey: text("api_key"),
  project: text("project"),
  synced: integer("synced", { mode: "boolean" }).notNull().default(false),
  createdAt: text("created_at").notNull(),
});

export const costBaselines = sqliteTable("cost_baselines", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  matchLevel: text("match_level").notNull(),
  action: text("action").notNull(),
  phaseLabelTokens: text("phase_label_tokens").default(""),
  avgCost: real("avg_cost").notNull(),
  ciLo: real("ci_lo").notNull(),
  ciHi: real("ci_hi").notNull(),
  sampleCount: integer("sample_count").notNull(),
  successRate: real("success_rate").notNull(),
  updatedAt: text("updated_at").notNull(),
});

export const completedSequences = sqliteTable("completed_sequences", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  goalTokens: text("goal_tokens").notNull(),
  contextTokens: text("context_tokens").default(""),
  actionSequence: text("action_sequence").notNull(),
  totalExpected: real("total_expected").notNull(),
  totalActual: real("total_actual").notNull(),
  efficiency: real("efficiency").notNull(),
  outcome: text("outcome").notNull(),
  createdAt: text("created_at").notNull(),
});
