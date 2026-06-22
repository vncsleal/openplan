import { randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir, platform } from "node:os";
import { join } from "node:path";
import { parse, stringify } from "smol-toml";

interface MeshSection {
  enabled?: boolean;
  url?: string;
  api_key?: string;
}

interface CostProbeSection {
  command?: string;
}

function readMeshSection(doc: TomlRaw): MeshSection | undefined {
  const raw = doc.mesh;
  if (!raw || typeof raw !== "object") return undefined;
  const obj = raw as Record<string, unknown>;
  return {
    enabled: typeof obj.enabled === "boolean" ? obj.enabled : undefined,
    url: typeof obj.url === "string" ? obj.url : undefined,
    api_key: typeof obj.api_key === "string" ? obj.api_key : undefined,
  };
}

function readCostProbeSection(doc: TomlRaw): CostProbeSection | undefined {
  const raw = doc.cost_probe;
  if (!raw || typeof raw !== "object") return undefined;
  const obj = raw as Record<string, unknown>;
  return {
    command: typeof obj.command === "string" ? obj.command : undefined,
  };
}

type TomlRaw = Record<string, unknown>;

export const DEFAULT_MESH_URL = "https://api.openplan.cc";

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

  let doc: TomlRaw = {};
  if (existsSync(configPath)) {
    try {
      const raw = parse(readFileSync(configPath, "utf-8"));
      doc = raw as TomlRaw;
    } catch (e) {
      console.error(`[openplan] Failed to parse config: ${e instanceof Error ? e.message : "unknown error"}`);
      doc = {};
    }
  }

  const meshSection = readMeshSection(doc);
  const costProbeSection = readCostProbeSection(doc);

  const meshEnabled = meshSection?.enabled !== false;

  return {
    identityId,
    projectRoot: process.env.OPENPLAN_PROJECT_ROOT ?? process.cwd(),
    dataDir: getDataDir(),
    meshUrl: meshEnabled ? (process.env.OPENPLAN_MESH_URL ?? meshSection?.url ?? DEFAULT_MESH_URL) : null,
    apiKey: process.env.OPENPLAN_API_KEY ?? meshSection?.api_key ?? null,
    costProbeCommand: process.env.OPENPLAN_COST_PROBE ?? costProbeSection?.command ?? null,
  };
}

export function saveConfig(partial: Partial<OpenPlanConfig>): void {
  const configPath = getConfigPath();
  let doc: TomlRaw = {};
  if (existsSync(configPath)) {
    try {
      const raw = parse(readFileSync(configPath, "utf-8"));
      doc = raw as TomlRaw;
    } catch (e) {
      console.error(`[openplan] Failed to parse config for save: ${e instanceof Error ? e.message : "unknown error"}`);
      doc = {};
    }
  }

  const currentMesh = readMeshSection(doc);

  if (partial.meshUrl !== undefined || partial.apiKey !== undefined || partial.meshUrl === null) {
    doc.mesh = {
      ...(currentMesh ?? {}),
      url: partial.meshUrl ?? currentMesh?.url ?? DEFAULT_MESH_URL,
      api_key: partial.apiKey ?? currentMesh?.api_key,
      enabled: partial.meshUrl !== null,
    };
  }

  if (partial.costProbeCommand !== undefined) {
    const currentProbe = readCostProbeSection(doc);
    doc.cost_probe = {
      command: partial.costProbeCommand ?? currentProbe?.command,
    };
  }

  writeFileSync(configPath, stringify(doc), "utf-8");
}
