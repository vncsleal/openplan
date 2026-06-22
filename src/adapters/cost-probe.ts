import { execSync } from "node:child_process";
import { existsSync, readdirSync } from "node:fs";
import { homedir, platform } from "node:os";
import { join } from "node:path";
import Database from "better-sqlite3";
import type { CostProbe } from "../core/ports.js";

// ── OpenCode ────────────────────────────────────────────────
// Lê tokens diretamente do SQLite do OpenCode.

const OPENCODE_DB_PATHS: (() => string)[] = [
  // macOS / Linux CLI
  () => join(homedir(), ".local", "share", "opencode", "opencode.db"),
  // macOS Desktop
  () => join(homedir(), "Library", "Application Support", "ai.opencode.desktop", "opencode", "opencode.db"),
  // Windows
  () => (process.env.APPDATA ? join(process.env.APPDATA, "opencode", "opencode.db") : ""),
];

function findOpenCodeDb(): string {
  for (const getPath of OPENCODE_DB_PATHS) {
    const p = getPath();
    if (p && existsSync(p)) return p;
  }
  return "";
}

let _dbPath: string | undefined;

function opencodeDbPath(): string {
  if (_dbPath === undefined) {
    _dbPath = findOpenCodeDb();
    console.error(`[openplan] probe db path: "${_dbPath || "(empty)"}"`);
  }
  return _dbPath;
}

export function isOpenCodeAvailable(): boolean {
  return opencodeDbPath() !== "";
}

function latestSessionTokens(): number {
  const p = opencodeDbPath();
  if (!p) return 0;
  try {
    const db = new Database(p, { readonly: true });
    const row = db
      .prepare("SELECT tokens_input, tokens_output, tokens_reasoning FROM session ORDER BY time_updated DESC LIMIT 1")
      .get() as { tokens_input: number; tokens_output: number; tokens_reasoning: number } | undefined;
    db.close();
    if (!row) return 0;
    return (row.tokens_input ?? 0) + (row.tokens_output ?? 0) + (row.tokens_reasoning ?? 0);
  } catch {
    return 0;
  }
}

export function createOpenCodeCostProbe(): CostProbe {
  let baselineTokens = 0;

  return {
    start(): void {
      baselineTokens = latestSessionTokens();
      console.error(`[openplan] probe start: path=${opencodeDbPath()} tokens=${baselineTokens}`);
    },

    stop(): number | null {
      if (baselineTokens === 0) {
        console.error(`[openplan] probe stop: baseline=0 path=${opencodeDbPath()}`);
        return null;
      }
      const tokens = latestSessionTokens();
      const delta = tokens - baselineTokens;
      console.error(`[openplan] probe stop: tokens=${tokens} baseline=${baselineTokens} delta=${delta}`);
      baselineTokens = 0;
      return delta > 0 ? delta : null;
    },
  };
}

// ── Claude Code ─────────────────────────────────────────────
// Placeholder para quando o Claude expuser custo via env var.

export function createClaudeCostProbe(): CostProbe {
  let baseline = 0;
  return {
    start(): void {
      baseline = Number.parseFloat(process.env.CLAUDE_RUNNING_COST ?? "") || 0;
    },
    stop(): number | null {
      if (baseline === 0) return null;
      const current = Number.parseFloat(process.env.CLAUDE_RUNNING_COST ?? "") || 0;
      const delta = current - baseline;
      baseline = 0;
      return delta > 0 ? Math.round(delta * 1_000_000) : null;
    },
  };
}

// ── Cursor ─────────────────────────────────────────────────
// Placeholder para quando o Cursor expuser custo via env var.

export function createCursorCostProbe(): CostProbe {
  let baseline = 0;
  return {
    start(): void {
      baseline = Number.parseFloat(process.env.CURSOR_RUNNING_COST ?? "") || 0;
    },
    stop(): number | null {
      if (baseline === 0) return null;
      const current = Number.parseFloat(process.env.CURSOR_RUNNING_COST ?? "") || 0;
      const delta = current - baseline;
      baseline = 0;
      return delta > 0 ? Math.round(delta * 1_000_000) : null;
    },
  };
}

// ── Codex ──────────────────────────────────────────────────
// Lê tokens diretamente do SQLite local do Codex Desktop/CLI.

function codexHome(): string {
  return process.env.CODEX_HOME || join(homedir(), ".codex");
}

function findCodexStateDb(): string {
  const home = codexHome();
  try {
    const stateDbs = readdirSync(home)
      .map((name) => {
        const match = /^state_(\d+)\.sqlite$/.exec(name);
        return match ? { name, version: Number.parseInt(match[1] ?? "0", 10) } : null;
      })
      .filter((entry): entry is { name: string; version: number } => entry !== null)
      .sort((a, b) => b.version - a.version);

    for (const { name } of stateDbs) {
      const p = join(home, name);
      if (existsSync(p)) return p;
    }
  } catch {
    return "";
  }
  return "";
}

let _codexDbPath: string | undefined;

function codexDbPath(): string {
  if (_codexDbPath === undefined) {
    _codexDbPath = findCodexStateDb();
    console.error(`[openplan] codex probe db path: "${_codexDbPath || "(empty)"}"`);
  }
  return _codexDbPath;
}

export function isCodexAvailable(): boolean {
  return Boolean(process.env.CODEX_THREAD_ID && codexDbPath());
}

function currentCodexThreadTokens(): number | null {
  const threadId = process.env.CODEX_THREAD_ID;
  const p = codexDbPath();
  if (!threadId || !p) return null;

  let db: Database.Database | null = null;
  try {
    db = new Database(p, { readonly: true });
    const row = db.prepare("SELECT tokens_used FROM threads WHERE id = ?").get(threadId) as
      | { tokens_used: number }
      | undefined;
    if (!row || !Number.isFinite(row.tokens_used)) return null;
    return row.tokens_used;
  } catch {
    return null;
  } finally {
    db?.close();
  }
}

export function createCodexCostProbe(): CostProbe {
  let baselineTokens: number | null = null;

  return {
    start(): void {
      baselineTokens = currentCodexThreadTokens();
      console.error(`[openplan] codex probe start: path=${codexDbPath()} tokens=${baselineTokens ?? "(null)"}`);
    },

    stop(): number | null {
      if (baselineTokens === null) {
        console.error(`[openplan] codex probe stop: baseline=null path=${codexDbPath()}`);
        return null;
      }

      const tokens = currentCodexThreadTokens();
      if (tokens === null) {
        baselineTokens = null;
        return null;
      }

      const delta = tokens - baselineTokens;
      console.error(`[openplan] codex probe stop: tokens=${tokens} baseline=${baselineTokens} delta=${delta}`);
      baselineTokens = null;
      return delta > 0 ? delta : null;
    },
  };
}

// ── Shell ───────────────────────────────────────────────────
// Comando externo configurado pelo usuário.

export function createShellCostProbe(command: string): CostProbe {
  return {
    start(): void {},
    stop(): number | null {
      try {
        const out = execSync(command, { encoding: "utf-8", timeout: 5000 }).toString().trim();
        if (out.length === 0) return null;
        const parsed = Number(out);
        return Number.isFinite(parsed) ? Math.round(parsed) : null;
      } catch {
        return null;
      }
    },
  };
}

// ── Nulo ───────────────────────────────────────────────────
// Fallback quando nenhum probe está disponível.

export function createNullCostProbe(): CostProbe {
  return {
    start(): void {},
    stop(): number | null {
      return null;
    },
  };
}
