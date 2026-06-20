export function createLogger(module: string) {
  const prefix = `[openplan:${module}]`;

  return {
    debug(message: string, err?: unknown) {
      const detail = err instanceof Error ? ` — ${err.message}` : "";
      console.error(`${prefix} ${message}${detail}`);
    },
    warn(message: string, err?: unknown) {
      const detail = err instanceof Error ? ` — ${err.message}` : "";
      console.error(`${prefix} ${message}${detail}`);
    },
    error(message: string, err?: unknown) {
      const detail = err instanceof Error ? ` — ${err.message}` : "";
      console.error(`${prefix} ${message}${detail}`);
    },
  };
}
