import { readFileSync, existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { homedir, platform } from "node:os";
import { parse, stringify } from "smol-toml";
import { randomUUID } from "node:crypto";

type TomlDoc = Record<string, unknown>;

export interface OpenPlanConfig {
  identityId: string;
  projectRoot: string;
  dataDir: string;
  meshUrl: string | null;
  apiKey: string | null;
  costProbeCommand: string | null;
}

function xdgConfigHome(): string {
  if (process.env.XDG_CONFIG_HOME) return process.env.XDG_CONFIG_HOME;
  if (platform() === "darwin") return join(homedir(), "Library", "Application Support");
  return join(homedir(), ".config");
}

function xdgDataHome(): string {
  if (process.env.XDG_DATA_HOME) return process.env.XDG_DATA_HOME;
  if (platform() === "darwin") return join(homedir(), "Library", "Application Support");
  return join(homedir(), ".local", "share");
}

export function getConfigPath(): string {
  return join(xdgConfigHome(), "openplan", "config.toml");
}

export function getDataDir(): string {
  return join(xdgDataHome(), "openplan");
}

export function ensureDirectories(): void {
  const configDir = join(xdgConfigHome(), "openplan");
  const dataDir = getDataDir();
  if (!existsSync(configDir)) mkdirSync(configDir, { recursive: true });
  if (!existsSync(dataDir)) mkdirSync(dataDir, { recursive: true });
}

function loadOrCreateIdentity(): string {
  const identityFile = join(getDataDir(), "identity");
  if (existsSync(identityFile)) {
    return readFileSync(identityFile, "utf-8").trim();
  }
  const id = randomUUID();
  writeFileSync(identityFile, id, "utf-8");
  return id;
}

export function loadConfig(): OpenPlanConfig {
  ensureDirectories();
  const configPath = getConfigPath();
  const identityId = loadOrCreateIdentity();

  let config: TomlDoc = {};
  if (existsSync(configPath)) {
    try {
      config = parse(readFileSync(configPath, "utf-8")) as TomlDoc;
    } catch (e) {
      console.error(`[openplan] Failed to parse config: ${e instanceof Error ? e.message : "unknown error"}`);
      config = {};
    }
  }

  const meshSection = config.mesh as Record<string, unknown> | undefined;
  const costProbeSection = config.cost_probe as Record<string, unknown> | undefined;

  return {
    identityId,
    projectRoot: process.env.OPENPLAN_PROJECT_ROOT ?? process.cwd(),
    dataDir: getDataDir(),
    meshUrl: process.env.OPENPLAN_MESH_URL ?? (meshSection?.url as string | undefined) ?? null,
    apiKey: process.env.OPENPLAN_API_KEY ?? (meshSection?.api_key as string | undefined) ?? null,
    costProbeCommand: process.env.OPENPLAN_COST_PROBE ?? (costProbeSection?.command as string | undefined) ?? null,
  };
}

export function saveConfig(partial: Partial<OpenPlanConfig>): void {
  const configPath = getConfigPath();
  let existing: TomlDoc = {};
  if (existsSync(configPath)) {
    try {
      existing = parse(readFileSync(configPath, "utf-8")) as TomlDoc;
    } catch (e) {
      console.error(`[openplan] Failed to parse config for save: ${e instanceof Error ? e.message : "unknown error"}`);
      existing = {};
    }
  }

  if (partial.meshUrl !== undefined || partial.apiKey !== undefined) {
    const currentMesh = existing.mesh as Record<string, unknown> | undefined;
    existing.mesh = {
      url: partial.meshUrl ?? (currentMesh?.url as string | undefined),
      api_key: partial.apiKey ?? (currentMesh?.api_key as string | undefined),
    };
  }

  if (partial.costProbeCommand !== undefined) {
    const currentProbe = existing.cost_probe as Record<string, unknown> | undefined;
    existing.cost_probe = {
      command: partial.costProbeCommand ?? (currentProbe?.command as string | undefined),
    };
  }

  writeFileSync(configPath, stringify(existing), "utf-8");
}
