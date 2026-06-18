import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import * as schema from "./schema.js";
import type { BetterSQLite3Database } from "drizzle-orm/better-sqlite3";
import type { ExtractTablesWithRelations } from "drizzle-orm";

type OpenPlanDb = BetterSQLite3Database<typeof schema> & { $client: Database.Database };

let db: OpenPlanDb | null = null;
let sqlite: Database.Database | null = null;

export function openDatabase(dbPath: string): OpenPlanDb {
  sqlite = new Database(dbPath);
  sqlite.pragma("journal_mode = WAL");
  sqlite.pragma("foreign_keys = ON");
  const instance = drizzle(sqlite, { schema }) as OpenPlanDb;
  db = instance;
  runMigrations(sqlite);
  return instance;
}

export function openInMemoryDatabase(): OpenPlanDb {
  sqlite = new Database(":memory:");
  sqlite.pragma("journal_mode = WAL");
  sqlite.pragma("foreign_keys = ON");
  const instance = drizzle(sqlite, { schema }) as OpenPlanDb;
  db = instance;
  runMigrations(sqlite);
  return instance;
}

export function getDb(): OpenPlanDb {
  if (!db) throw new Error("Database not initialized. Call openDatabase() first.");
  return db;
}

export function closeDatabase(): void {
  if (sqlite) {
    sqlite.close();
    sqlite = null;
    db = null;
  }
}

function runMigrations(sqlite: Database.Database): void {
  sqlite.exec(`
    CREATE TABLE IF NOT EXISTS schema_version (
      version INTEGER NOT NULL
    );

    INSERT OR IGNORE INTO schema_version (version) VALUES (1);

    CREATE TABLE IF NOT EXISTS routes (
      id TEXT PRIMARY KEY,
      project TEXT NOT NULL,
      goal TEXT NOT NULL,
      goal_tokens TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'active',
      identity_id TEXT NOT NULL,
      total_expected REAL,
      total_actual REAL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS route_phases (
      id TEXT PRIMARY KEY,
      route_id TEXT NOT NULL REFERENCES routes(id),
      label TEXT NOT NULL,
      label_tokens TEXT NOT NULL,
      action TEXT NOT NULL,
      expected_cost REAL,
      actual_cost REAL,
      status TEXT NOT NULL DEFAULT 'pending',
      sequence INTEGER NOT NULL,
      hazards TEXT,
      deviation REAL,
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS calibration_events (
      id TEXT PRIMARY KEY,
      action TEXT NOT NULL,
      phase_label_tokens TEXT,
      expected_cost REAL NOT NULL,
      actual_cost REAL NOT NULL,
      outcome TEXT NOT NULL,
      identity_id TEXT NOT NULL,
      route_id TEXT,
      phase_id TEXT,
      synced INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS correction_events (
      id TEXT PRIMARY KEY,
      calibration_event_id TEXT NOT NULL REFERENCES calibration_events(id),
      previous_actual REAL NOT NULL,
      corrected_actual REAL NOT NULL,
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS cost_baselines (
      id TEXT PRIMARY KEY,
      match_level TEXT NOT NULL,
      action TEXT NOT NULL,
      avg_cost REAL NOT NULL,
      ci_lo REAL,
      ci_hi REAL,
      sample_count INTEGER NOT NULL,
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS completed_sequences (
      id TEXT PRIMARY KEY,
      action_sequence TEXT NOT NULL,
      total_expected REAL NOT NULL,
      total_actual REAL NOT NULL,
      efficiency REAL NOT NULL,
      created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_routes_project ON routes(project);
    CREATE INDEX IF NOT EXISTS idx_routes_status ON routes(status);
    CREATE INDEX IF NOT EXISTS idx_route_phases_route_id ON route_phases(route_id);
    CREATE INDEX IF NOT EXISTS idx_calibration_events_identity ON calibration_events(identity_id);
    CREATE INDEX IF NOT EXISTS idx_calibration_events_synced ON calibration_events(synced);
  `);
}
