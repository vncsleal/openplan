import { readFileSync, existsSync, mkdirSync, writeFileSync, chmodSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { parse, stringify } from "smol-toml";

export interface AppConfig {
  core: {
    dbPath: string;
  };
  mesh: {
    apiUrl: string;
    apiKey: string;
  };
  costProbe?: {
    command: string;
  };
}

const CONFIG_DIR = join(homedir(), ".config", "openplan");
const CONFIG_PATH = join(CONFIG_DIR, "config.toml");

function getDefaultConfig(): AppConfig {
  return {
    core: {
      dbPath: join(homedir(), ".local", "share", "openplan", "data.db"),
    },
    mesh: {
      apiUrl: "https://api.openplan.cc",
      apiKey: "",
    },
  };
}

export function getConfigPath(): string {
  return process.env.OPENPLAN_CONFIG || CONFIG_PATH;
}

export function loadConfig(): AppConfig {
  const configPath = getConfigPath();
  const envOverrides = getEnvOverrides();
  const config = getDefaultConfig();

  // Merge config file
  if (existsSync(configPath)) {
    try {
      const raw = readFileSync(configPath, "utf-8");
      const parsed = parse(raw) as Record<string, unknown>;

      if (parsed.core && typeof parsed.core === "object") {
        const core = parsed.core as Record<string, unknown>;
        if (typeof core.dbPath === "string") config.core.dbPath = core.dbPath.replace(/^~/, homedir());
      }
      if (parsed.mesh && typeof parsed.mesh === "object") {
        const mesh = parsed.mesh as Record<string, unknown>;
        if (typeof mesh.apiUrl === "string") config.mesh.apiUrl = mesh.apiUrl;
        if (typeof mesh.apiKey === "string") config.mesh.apiKey = mesh.apiKey;
      }
      if (parsed.costProbe && typeof parsed.costProbe === "object") {
        const cp = parsed.costProbe as Record<string, unknown>;
        if (typeof cp.command === "string") config.costProbe = { command: cp.command };
      }
    } catch (err) {
      console.error(`Failed to parse config at ${configPath}:`, (err as Error).message);
      process.exit(1);
    }
  }

  // Merge env overrides
  if (envOverrides.dbPath) config.core.dbPath = envOverrides.dbPath.replace(/^~/, homedir());
  if (envOverrides.apiUrl) config.mesh.apiUrl = envOverrides.apiUrl;
  if (envOverrides.apiKey) config.mesh.apiKey = envOverrides.apiKey;
  if (envOverrides.costProbeCommand) config.costProbe = { command: envOverrides.costProbeCommand };

  // Legacy env var aliases
  if (process.env.OPENPLAN_API_URL && !envOverrides.apiUrl) {
    config.mesh.apiUrl = process.env.OPENPLAN_API_URL;
  }
  if (process.env.OPENPLAN_API_KEY && !envOverrides.apiKey) {
    config.mesh.apiKey = process.env.OPENPLAN_API_KEY;
  }
  if (process.env.OPENPLAN_DB_PATH && !envOverrides.dbPath) {
    config.core.dbPath = process.env.OPENPLAN_DB_PATH.replace(/^~/, homedir());
  }

  return config;
}

export function ensureConfig(): void {
  const configPath = getConfigPath();
  if (existsSync(configPath)) return;

  // Create config directory
  mkdirSync(CONFIG_DIR, { recursive: true });

  // Write default config
  const config = getDefaultConfig();
  const toml = stringify({
    core: { dbPath: config.core.dbPath },
    mesh: { apiUrl: config.mesh.apiUrl, apiKey: config.mesh.apiKey },
  } as Record<string, unknown>);

  writeFileSync(configPath, toml, "utf-8");
  chmodSync(configPath, 0o600);

  // Ensure data directory exists
  const dataDir = join(homedir(), ".local", "share", "openplan");
  mkdirSync(dataDir, { recursive: true });
}

export function saveConfig(config: AppConfig): void {
  const configPath = getConfigPath();
  const toml = stringify({
    core: { dbPath: config.core.dbPath },
    mesh: { apiUrl: config.mesh.apiUrl, apiKey: config.mesh.apiKey },
    ...(config.costProbe ? { costProbe: { command: config.costProbe.command } } : {}),
  } as Record<string, unknown>);
  writeFileSync(configPath, toml, "utf-8");
  chmodSync(configPath, 0o600);
}

function getEnvOverrides(): Record<string, string | undefined> {
  return {
    dbPath: process.env.OPENPLAN_CORE__DB_PATH,
    apiUrl: process.env.OPENPLAN_MESH__API_URL,
    apiKey: process.env.OPENPLAN_MESH__API_KEY,
    costProbeCommand: process.env.OPENPLAN_COST_PROBE__COMMAND,
  };
}
