import { appendFileSync, existsSync, mkdirSync } from "node:fs";
import { homedir, platform } from "node:os";
import { join } from "node:path";

function xdgDataHome(): string {
  if (process.env.XDG_DATA_HOME) return process.env.XDG_DATA_HOME;
  if (platform() === "darwin") return join(homedir(), "Library", "Application Support");
  return join(homedir(), ".local", "share");
}

function ensureLogDir(): string {
  const dir = join(xdgDataHome(), "openplan", "logs");
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  return dir;
}

function isoNow(): string {
  return new Date().toISOString();
}

export function createLogger(module: string) {
  const prefix = `[openplan:${module}]`;
  const LOG_DIR_FAILED = "";
  let logDir: string | null = null;

  function log(level: string, message: string, err?: unknown) {
    const detail = err instanceof Error ? ` — ${err.message}` : "";
    const line = `${isoNow()} ${prefix} [${level}] ${message}${detail}`;
    console.error(line);
    if (logDir === LOG_DIR_FAILED) return;
    if (!logDir) {
      try {
        logDir = ensureLogDir();
      } catch (e) {
        logDir = LOG_DIR_FAILED;
        console.error(`[openplan] Failed to create log directory: ${e instanceof Error ? e.message : String(e)}`);
        return;
      }
    }
    try {
      appendFileSync(join(logDir, `${module}.log`), `${line}\n`, "utf-8");
    } catch (e) {
      console.error(`[openplan] Failed to write to log file: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  return {
    debug(message: string, err?: unknown) {
      log("DEBUG", message, err);
    },
    info(message: string, err?: unknown) {
      log("INFO", message, err);
    },
    warn(message: string, err?: unknown) {
      log("WARN", message, err);
    },
    error(message: string, err?: unknown) {
      log("ERROR", message, err);
    },
  };
}
