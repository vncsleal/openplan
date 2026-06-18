import Database from "better-sqlite3";

let _sqlite: Database.Database | null = null;

export function getConnection(dbPath: string): Database.Database {
  if (_sqlite) return _sqlite;

  _sqlite = new Database(dbPath);
  _sqlite.pragma("journal_mode = WAL");
  _sqlite.pragma("foreign_keys = ON");

  initTables(_sqlite);
  seedDefaults(_sqlite);

  return _sqlite;
}

export function initTables(sqlite: Database.Database): void {
  sqlite.exec(`
    CREATE TABLE IF NOT EXISTS routes (
      id TEXT PRIMARY KEY,
      project TEXT NOT NULL,
      goal TEXT NOT NULL,
      context TEXT DEFAULT '',
      total_expected REAL NOT NULL,
      total_actual REAL DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'active',
      archived INTEGER NOT NULL DEFAULT 0,
      abandon_reason TEXT,
      completed_at TEXT,
      goal_tokens TEXT DEFAULT '',
      context_tokens TEXT DEFAULT '',
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS route_phases (
      id TEXT PRIMARY KEY,
      route_id TEXT NOT NULL REFERENCES routes(id),
      label TEXT NOT NULL,
      action TEXT NOT NULL,
      expected_cost REAL NOT NULL,
      actual_cost REAL,
      outcome TEXT,
      status TEXT NOT NULL DEFAULT 'pending',
      sequence INTEGER NOT NULL,
      label_tokens TEXT DEFAULT '',
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS calibration_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      action TEXT NOT NULL,
      phase_label_tokens TEXT NOT NULL,
      expected_cost REAL NOT NULL,
      actual_cost REAL NOT NULL,
      outcome TEXT NOT NULL,
      api_key TEXT,
      project TEXT,
      synced INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS cost_baselines (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      match_level TEXT NOT NULL,
      action TEXT NOT NULL,
      phase_label_tokens TEXT DEFAULT '',
      avg_cost REAL NOT NULL,
      ci_lo REAL NOT NULL,
      ci_hi REAL NOT NULL,
      sample_count INTEGER NOT NULL,
      success_rate REAL NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS completed_sequences (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      goal_tokens TEXT NOT NULL,
      context_tokens TEXT DEFAULT '',
      action_sequence TEXT NOT NULL,
      total_expected REAL NOT NULL,
      total_actual REAL NOT NULL,
      efficiency REAL NOT NULL,
      outcome TEXT NOT NULL,
      created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_routes_project ON routes(project);
    CREATE INDEX IF NOT EXISTS idx_route_phases_route ON route_phases(route_id);
    CREATE INDEX IF NOT EXISTS idx_calibration_synced ON calibration_events(synced);
    CREATE INDEX IF NOT EXISTS idx_baselines_action ON cost_baselines(action);
    CREATE INDEX IF NOT EXISTS idx_sequences_goals ON completed_sequences(goal_tokens);
  `);
}

export function seedDefaults(sqlite: Database.Database): void {
  const count = sqlite.prepare("SELECT COUNT(*) as cnt FROM cost_baselines").get() as { cnt: number };
  if (count.cnt > 0) return;

  const now = new Date().toISOString();
  const defaults = [
    { level: "default", action: "implement", tokens: "", avg: 2000, lo: 500, hi: 5000, samples: 100, rate: 0.8 },
    { level: "default", action: "design", tokens: "", avg: 1500, lo: 500, hi: 4000, samples: 50, rate: 0.85 },
    { level: "default", action: "deploy", tokens: "", avg: 600, lo: 300, hi: 1000, samples: 80, rate: 0.9 },
    { level: "default", action: "test", tokens: "", avg: 800, lo: 400, hi: 1500, samples: 60, rate: 0.85 },
  ];

  const stmt = sqlite.prepare(
    "INSERT INTO cost_baselines (match_level, action, phase_label_tokens, avg_cost, ci_lo, ci_hi, sample_count, success_rate, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
  );

  for (const d of defaults) {
    stmt.run(d.level, d.action, d.tokens, d.avg, d.lo, d.hi, d.samples, d.rate, now);
  }
}

export function close(): void {
  if (_sqlite) {
    _sqlite.close();
    _sqlite = null;
  }
}
