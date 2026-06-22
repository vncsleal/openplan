import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import type { BetterSQLite3Database } from "drizzle-orm/better-sqlite3";
import * as schema from "./schema.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

type OpenPlanDb = BetterSQLite3Database<typeof schema> & { $client: Database.Database };

let db: OpenPlanDb | null = null;
let sqlite: Database.Database | null = null;

function migrationsDir(): string {
  return join(__dirname, "..", "..", "drizzle");
}

function migrationsMetaDir(): string {
  return join(migrationsDir(), "meta");
}

export function openDatabase(dbPath: string): OpenPlanDb {
  if (db || sqlite) {
    throw new Error("Database already initialized — close the existing connection first");
  }
  sqlite = new Database(dbPath);
  sqlite.pragma("journal_mode = WAL");
  sqlite.pragma("foreign_keys = ON");
  const instance = drizzle(sqlite, { schema }) as OpenPlanDb;
  db = instance;
  runMigrations(sqlite);
  return instance;
}

export function openInMemoryDatabase(): OpenPlanDb {
  if (db || sqlite) {
    throw new Error("Database already initialized — close the existing connection first");
  }
  sqlite = new Database(":memory:");
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

export function resetDatabaseForTesting(): void {
  if (sqlite) {
    sqlite.close();
  }
  sqlite = null;
  db = null;
}

const MIGRATIONS_TABLE = "__drizzle_migrations";

function runMigrations(sqlite: Database.Database): void {
  const migrationsPath = migrationsDir();
  if (!existsSync(migrationsPath)) {
    sqlite.exec(`
      CREATE TABLE IF NOT EXISTS "${MIGRATIONS_TABLE}" (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hash TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL
      )
    `);
    return;
  }

  sqlite.exec(`
    CREATE TABLE IF NOT EXISTS "${MIGRATIONS_TABLE}" (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      hash TEXT NOT NULL UNIQUE,
      created_at TEXT NOT NULL
    )
  `);

  const metaPath = migrationsMetaDir();
  if (!existsSync(metaPath)) return;

  const journalPath = join(metaPath, "_journal.json");
  if (!existsSync(journalPath)) return;

  const files = readFileSync(journalPath, "utf-8");
  const journal = JSON.parse(files) as { entries: { when?: number; idx: number; tag: string }[] };

  // Check if tables already exist (from a previous version's CREATE TABLE IF NOT EXISTS)
  // Exclude internal tables: __drizzle_* and sqlite_*
  const tableCount = sqlite
    .prepare(
      "SELECT COUNT(*) AS cnt FROM sqlite_master WHERE type='table' AND name NOT LIKE '__drizzle_%' AND name NOT LIKE 'sqlite_%'",
    )
    .get() as { cnt: number };
  if (tableCount.cnt > 0) {
    // Tables already exist — mark all journal entries as applied and skip
    for (const entry of journal.entries) {
      const hash = entry.tag;
      const existing = sqlite.prepare(`SELECT id FROM "${MIGRATIONS_TABLE}" WHERE hash = ?`).get(hash);
      if (!existing) {
        sqlite
          .prepare(`INSERT OR IGNORE INTO "${MIGRATIONS_TABLE}" (hash, created_at) VALUES (?, ?)`)
          .run(hash, new Date().toISOString());
      }
    }
    return;
  }

  for (const entry of journal.entries) {
    const hash = entry.tag;
    const existing = sqlite.prepare(`SELECT id FROM "${MIGRATIONS_TABLE}" WHERE hash = ?`).get(hash);
    if (existing) continue;

    const migrationFile = join(migrationsPath, `${entry.tag}.sql`);
    if (!existsSync(migrationFile)) continue;

    const sql = readFileSync(migrationFile, "utf-8");
    sqlite.exec(sql);
    sqlite
      .prepare(`INSERT INTO "${MIGRATIONS_TABLE}" (hash, created_at) VALUES (?, ?)`)
      .run(hash, new Date().toISOString());
  }
}
