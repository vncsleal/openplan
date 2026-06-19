#!/usr/bin/env node
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Command } from "commander";
import pc from "picocolors";
import { parse, stringify } from "smol-toml";
import { ensureDirectories, getConfigPath, getDataDir, loadConfig, saveConfig } from "./config.js";
import { openDatabase } from "./db/connection.js";
import { createStore } from "./db/store.js";
import { startServer } from "./server.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const pkg = JSON.parse(readFileSync(join(__dirname, "..", "package.json"), "utf-8")) as { version: string };

const program = new Command();

program
  .name("openplan")
  .description("Waze for AI agents -- plan, track, and learn from software projects")
  .version(pkg.version)
  .option("--json", "Output in JSON format")
  .option("--no-color", "Disable color output");

function meshUrl(): string {
  return process.env.OPENPLAN_MESH_URL ?? "https://api.openplan.cc";
}

// ── install ───────────────────────────────────────────────────────────────────

program
  .command("install")
  .description("Detect MCP clients and install OpenPlan")
  .action(async () => {
    const openplanEntry = { command: "npx", args: ["-y", "@openplan/mcp"] };
    const openplanLocal = { type: "local", command: ["npx", "-y", "@openplan/mcp"], enabled: true };

    const opencodeDir = process.env.XDG_CONFIG_HOME
      ? join(process.env.XDG_CONFIG_HOME, "opencode")
      : join(process.env.HOME ?? "/tmp", ".config", "opencode");
    const opencodeConfig = join(opencodeDir, "opencode.json");
    const hasOpencode = existsSync(opencodeConfig);

    const claudeConfig = join(
      process.env.HOME ?? "/tmp",
      "Library",
      "Application Support",
      "Claude",
      "claude_desktop_config.json",
    );
    const hasClaude = existsSync(claudeConfig);

    if (!hasOpencode && !hasClaude) {
      console.error(`${pc.red("!")} No supported MCP clients detected.`);
      console.error("  Install OpenCode or Claude Desktop, then run `openplan install` again.");
      process.exit(1);
    }

    const detected: string[] = [];
    if (hasOpencode) detected.push("OpenCode");
    if (hasClaude) detected.push("Claude Desktop");
    console.error(`  ${pc.dim(">")} Detected: ${detected.join(", ")}`);

    // Ask for consent before writing configs (skip in CI)
    if (process.stdout.isTTY && !process.env.CI) {
      const { createInterface } = await import("node:readline");
      const rl = createInterface({ input: process.stdin, output: process.stderr });
      const answer = await new Promise<string>((resolve) => {
        rl.question(`  ${pc.dim(">")} Install in ${detected.join(" and ")}? [Y/n] `, resolve);
      });
      rl.close();
      if (answer.trim().toLowerCase() === "n") {
        console.error(`  ${pc.yellow(">")} Installation cancelled.\n`);
        process.exit(0);
      }
    }

    for (const client of detected) {
      if (client === "OpenCode") {
        try {
          const raw = readFileSync(opencodeConfig, "utf-8");
          const cfg = JSON.parse(raw);
          if (!cfg.mcp) cfg.mcp = {};
          if (!cfg.mcp.openplan) {
            cfg.mcp.openplan = openplanLocal;
            writeFileSync(opencodeConfig, JSON.stringify(cfg, null, 2), "utf-8");
            console.error(`  ${pc.green("*")} Installed in OpenCode`);
          } else {
            console.error(`  ${pc.yellow(">")} OpenPlan already configured in OpenCode`);
          }
        } catch (e) {
          console.error(
            `  ${pc.red("!")} Failed to update OpenCode: ${e instanceof Error ? e.message : "unknown error"}`,
          );
        }
      }
      if (client === "Claude Desktop") {
        try {
          const raw = readFileSync(claudeConfig, "utf-8");
          const cfg = JSON.parse(raw);
          if (!cfg.mcpServers) cfg.mcpServers = {};
          if (!cfg.mcpServers.openplan) {
            cfg.mcpServers.openplan = openplanEntry;
            writeFileSync(claudeConfig, JSON.stringify(cfg, null, 2), "utf-8");
            console.error(`  ${pc.green("*")} Installed in Claude Desktop`);
          } else {
            console.error(`  ${pc.yellow(">")} OpenPlan already configured in Claude Desktop`);
          }
        } catch (e) {
          console.error(
            `  ${pc.red("!")} Failed to update Claude Desktop: ${e instanceof Error ? e.message : "unknown error"}`,
          );
        }
      }
    }

    console.error(`\n  ${pc.green("*")} OpenPlan is ready. Restart your MCP client.`);
  });

// ── auth ─────────────────────────────────────────────────────────────────────

program
  .command("auth")
  .description("Authenticate with OpenPlan Mesh (GitHub OAuth)")
  .option("--no-browser", "Do not open browser automatically")
  .option("--clipboard", "Copy code to clipboard")
  .option("--debug", "Show detailed API responses for troubleshooting")
  .option("--with-token <token>", "Use an existing API key directly (for CI/headless)")
  .action(async (options: { browser: boolean; clipboard: boolean; debug: boolean; withToken: string }) => {
    const base = meshUrl();

    if (options.withToken) {
      saveConfig({ apiKey: options.withToken, meshUrl: base });
      console.error(`  ${pc.green("*")} API key saved to config.\n`);
      return;
    }

    const isInteractive = process.stdout.isTTY && !process.env.CI;

    process.on("SIGINT", () => {
      process.stderr.write("\n");
      console.error(`  ${pc.yellow("!")} Authentication cancelled.\n`);
      process.exit(0);
    });

    try {
      const deviceResp = await fetch(`${base}/v1/auth/device`, { method: "POST" });
      if (!deviceResp.ok) throw new Error(`Device auth failed (${deviceResp.status})`);
      const device = (await deviceResp.json()) as Record<string, unknown>;
      if (options.debug) console.error(`  ${pc.dim("[debug]")} device: ${JSON.stringify(device)}`);

      const userCode = device.user_code as string;
      const verificationUri = (device.verification_uri as string) ?? "https://github.com/login/device";
      let interval = (device.interval as number) ?? 5;
      const deviceCode = device.device_code as string;
      const expiresIn = (device.expires_in as number) ?? 900;
      const expiryMinutes = Math.floor(expiresIn / 60);

      console.error("");
      console.error(`  ${pc.bold("OpenPlan Mesh Authentication")}`);
      console.error("");
      console.error(`  ${pc.dim(">")}  Open this URL in your browser:`);
      console.error(`     ${pc.cyan(verificationUri)}`);
      console.error("");
      console.error(`  ${pc.dim(">")}  Then enter the code:  ${pc.bold(pc.bgGreen(pc.black(` ${userCode} `)))}`);
      console.error(`     ${pc.dim("-")}  Expires in ${expiryMinutes} minutes`);
      console.error("");

      if (options.clipboard) {
        try {
          const { execSync } = await import("node:child_process");
          const cmd =
            process.platform === "darwin"
              ? `echo "${userCode}" | pbcopy`
              : `echo "${userCode}" | xclip -selection clipboard`;
          execSync(cmd, { timeout: 2000 });
          console.error(`  ${pc.dim(">")}  Code copied to clipboard`);
          console.error("");
        } catch {
          /* clipboard unavailable */
        }
      }

      if (options.browser && isInteractive) {
        try {
          const { execSync } = await import("node:child_process");
          execSync(`open "${verificationUri}"`, { timeout: 3000 });
          console.error(`  ${pc.dim(">")}  Browser opened`);
          console.error("");
        } catch {
          /* fallback */
        }
      }

      const maxAttempts = Math.ceil(expiresIn / interval);
      const startTime = Date.now();
      let dots = 0;

      for (let i = 0; i < maxAttempts; i++) {
        const elapsed = Math.round((Date.now() - startTime) / 1000);
        const remaining = Math.max(0, expiryMinutes * 60 - elapsed);
        dots = (dots + 1) % 4;
        const dotStr = ".".repeat(dots) + " ".repeat(3 - dots);
        process.stderr.write(
          `\r  Waiting for GitHub authentication${dotStr}  ${pc.dim(`(${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")})`)}`,
        );

        await new Promise((r) => setTimeout(r, interval * 1000));

        const pollResp = await fetch(`${base}/v1/auth/device/poll`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ device_code: deviceCode }),
        });

        if (!pollResp.ok) {
          if (options.debug) {
            const text = await pollResp.text().catch(() => "");
            console.error(`\n  ${pc.dim("[debug]")} poll HTTP ${pollResp.status}: ${text.slice(0, 200)}`);
          }
          continue;
        }

        const poll = (await pollResp.json()) as Record<string, unknown>;
        if (options.debug) console.error(`\n  ${pc.dim("[debug]")} poll: ${JSON.stringify(poll)}`);

        if (poll.access_token) {
          process.stderr.write("\r                                                          \r");
          process.stderr.write(`  ${pc.green("*")} GitHub authentication complete!\n`);

          const keyResp = await fetch(`${base}/v1/api/keys`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${poll.access_token}`,
            },
            body: JSON.stringify({ tier: "free" }),
          });
          if (!keyResp.ok) throw new Error("Failed to create API key");
          const keyData = (await keyResp.json()) as Record<string, unknown>;
          if (options.debug) console.error(`  ${pc.dim("[debug]")} key: ${JSON.stringify(keyData)}`);

          saveConfig({ apiKey: keyData.api_key as string, meshUrl: base });
          console.error(`  ${pc.green("*")} Authenticated! API key saved to config.\n`);
          return;
        }

        if (poll.error === "authorization_pending") continue;
        if (poll.error === "slow_down") {
          interval += 5;
          continue;
        }
        if (poll.error === "expired_token") {
          process.stderr.write("\r                                                          \r");
          process.stderr.write(`  ${pc.red("!")} Session expired.\n`);
          console.error(`  ${pc.red("!")} Session expired. Run \`openplan auth\` again.\n`);
          return;
        }
        if (poll.error === "access_denied") {
          process.stderr.write("\r                                                          \r");
          process.stderr.write(`  ${pc.red("!")} Authorization denied.\n`);
          return;
        }

        process.stderr.write("\r                                                          \r");
        process.stderr.write(
          `  ${pc.red("!")} ${(poll.error_description as string) ?? (poll.error as string) ?? "Unknown error"}\n`,
        );
        return;
      }

      process.stderr.write("\r                                                          \r");
      process.stderr.write(`  ${pc.red("!")} Timed out.\n`);
      console.error(`  ${pc.red("!")} Timed out after ${expiryMinutes} minutes. Run \`openplan auth\` again.\n`);
    } catch (e) {
      console.error(`  ${pc.red("!")} ${e instanceof Error ? e.message : "unknown error"}\n`);
    }
  });

// ── subscribe ────────────────────────────────────────────────────────────────

program
  .command("subscribe")
  .description("Manage subscription (Stripe Checkout)")
  .argument("[plan]", "Plan: pro (default) or enterprise", "pro")
  .action(async (plan: string) => {
    const config = loadConfig();
    if (!config.apiKey) {
      console.error(`  ${pc.yellow("!")} Not authenticated. Run \`openplan auth\` first.\n`);
      process.exit(1);
    }

    const base = meshUrl();

    // Manage existing subscription (Stripe Customer Portal)
    if (plan === "manage") {
      try {
        const resp = await fetch(`${base}/v1/manage`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${config.apiKey}`,
          },
        });
        if (!resp.ok) {
          const err = (await resp.json().catch(() => null)) as Record<string, unknown> | null;
          console.error(`  ${pc.red("!")} ${err?.detail ? err.detail : `Manage failed (${resp.status})`}\n`);
          return;
        }
        const data = (await resp.json()) as Record<string, unknown>;
        const url = data.url as string;
        console.error(`  ${pc.dim(">")}  Opening billing portal...\n`);
        try {
          const { execSync } = await import("node:child_process");
          execSync(`open "${url}"`, { timeout: 3000 });
        } catch {
          /* fallback */
        }
      } catch (e) {
        console.error(`  ${pc.red("!")} Manage failed: ${e instanceof Error ? e.message : "unknown error"}\n`);
      }
      return;
    }

    try {
      const resp = await fetch(`${base}/v1/subscribe`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan, api_key: config.apiKey }),
      });
      if (!resp.ok) {
        const err = (await resp.json().catch(() => null)) as Record<string, unknown> | null;
        console.error(
          `  ${pc.red("!")} ${err?.detail ? `Subscribe failed: ${err.detail}` : `Subscribe failed (${resp.status})`}\n`,
        );
        return;
      }
      const data = (await resp.json()) as Record<string, unknown>;
      const url = data.checkout_url as string;

      console.error("");
      console.error(`  ${pc.bold("OpenPlan Subscription")}`);
      console.error("");
      console.error(`  ${pc.dim(">")}  Complete checkout at:`);
      console.error(`     ${pc.cyan(url)}`);
      console.error("");
      console.error(`  ${pc.dim("-")}  Your subscription activates automatically after payment.`);
      console.error("");

      if (process.stdout.isTTY && !process.env.CI) {
        try {
          const { execSync } = await import("node:child_process");
          execSync(`open "${url}"`, { timeout: 3000 });
          console.error(`  ${pc.dim(">")}  Browser opened\n`);
        } catch {
          /* fallback */
        }
      }
    } catch (e) {
      console.error(`  ${pc.red("!")} Subscribe failed: ${e instanceof Error ? e.message : "unknown error"}\n`);
    }
  });

// ── account ──────────────────────────────────────────────────────────────────

program
  .command("account")
  .description("Account info and subscription status")
  .argument("[action]", "Action: delete")
  .action(async (action?: string) => {
    const config = loadConfig();
    const base = meshUrl();

    // Account deletion
    if (action === "delete") {
      if (!config.apiKey) {
        console.error(`  ${pc.yellow("!")} Not authenticated. Run \`openplan auth\` first.\n`);
        return;
      }
      console.error(
        `  ${pc.yellow("!")} This will delete all your calibration data from the Mesh and revoke your API key.`,
      );
      try {
        const resp = await fetch(`${base}/v1/account/delete`, {
          method: "POST",
          headers: { Authorization: `Bearer ${config.apiKey}` },
        });
        if (!resp.ok) {
          console.error(`  ${pc.red("!")} Delete failed (${resp.status})\n`);
          return;
        }
        console.error(`  ${pc.green("*")} Account data deleted from Mesh.\n`);
      } catch (e) {
        console.error(`  ${pc.red("!")} Delete failed: ${e instanceof Error ? e.message : "unknown error"}\n`);
      }
      return;
    }

    let subStatus: Record<string, unknown> | null = null;
    if (config.apiKey) {
      try {
        const resp = await fetch(`${base}/v1/account`, {
          headers: { Authorization: `Bearer ${config.apiKey}` },
        });
        if (resp.ok) subStatus = (await resp.json()) as Record<string, unknown>;
      } catch {
        /* Mesh unreachable — show local-only info */
      }
    }

    if (program.opts().json) {
      console.log(
        JSON.stringify(
          {
            identityId: config.identityId,
            dataDir: config.dataDir,
            apiKey: config.apiKey ? "configured" : null,
            subscription: subStatus,
          },
          null,
          2,
        ),
      );
    } else {
      console.error(`  ${pc.dim("-")}  Identity: ${config.identityId}`);
      console.error(`  ${pc.dim("-")}  Data: ${config.dataDir}`);
      console.error(`  ${pc.dim("-")}  API Key: ${config.apiKey ? pc.green("configured") : pc.dim("not configured")}`);
      if (subStatus) {
        console.error(
          `  ${pc.dim("-")}  Subscription: ${(subStatus.tier as string) ?? "free"} — ${subStatus.status as string}`,
        );
      } else {
        console.error(`  ${pc.dim("-")}  Subscription: ${pc.dim("free (unauthenticated)")}`);
      }
      console.error("");
    }
  });

// ── config ───────────────────────────────────────────────────────────────────

program
  .command("config")
  .description("Display or modify configuration")
  .argument("[action]", "Action: show")
  .action((action?: string) => {
    if (action === "show" || !action) {
      const config = loadConfig();
      if (program.opts().json) {
        console.log(JSON.stringify(config, null, 2));
      } else {
        console.error(`  ${pc.dim("-")}  Config file: ${getConfigPath()}`);
        console.error(`  ${pc.dim("-")}  Data directory: ${getDataDir()}`);
        console.error(`  ${pc.dim("-")}  Identity: ${config.identityId}`);
        console.error(`  ${pc.dim("-")}  Mesh: ${config.meshUrl ? pc.green("enabled") : pc.dim("disabled")}`);
        console.error(`  ${pc.dim("-")}  Mesh URL: ${config.meshUrl ?? pc.dim("none")}`);
        console.error(
          `  ${pc.dim("-")}  API Key: ${config.apiKey ? pc.green("configured") : pc.dim("not configured")}`,
        );
        console.error(`  ${pc.dim("-")}  Cost Probe: ${config.costProbeCommand ?? pc.dim("not configured")}`);
        console.error("");
      }
    }
  });

// ── mesh ─────────────────────────────────────────────────────────────────────

program
  .command("mesh")
  .description("Show or toggle Mesh sync")
  .argument("[action]", "on | off")
  .action((action?: string) => {
    const config = loadConfig();
    const dbPath = join(getDataDir(), "openplan.db");

    if (action === "on") {
      saveConfig({ meshUrl: config.meshUrl ?? "https://api.openplan.cc" });
      console.error(`  ${pc.green("*")} Mesh sync enabled.\n`);
      return;
    }

    if (action === "off") {
      const tomlPath = getConfigPath();
      try {
        const raw = readFileSync(tomlPath, "utf-8");
        const doc = parse(raw) as Record<string, unknown>;
        const mesh = doc.mesh as Record<string, unknown> | undefined;
        doc.mesh = { ...(mesh ?? {}), enabled: false, url: config.meshUrl ?? "https://api.openplan.cc" };
        writeFileSync(tomlPath, stringify(doc), "utf-8");
        console.error(`  ${pc.green("*")} Mesh sync disabled.\n`);
      } catch (e) {
        console.error(`  ${pc.red("!")} Failed: ${e instanceof Error ? e.message : "unknown error"}\n`);
      }
      return;
    }

    // Status display
    const meshEnabled = config.meshUrl !== null;
    let pending = 0;
    let synced = 0;
    if (existsSync(dbPath)) {
      const db = openDatabase(dbPath);
      const store = createStore(db, config.identityId);
      pending = store.getUnsyncedCalibrationEvents().length;
      synced = store.getCalibrationEvents().length - pending;
    }
    console.error(`  ${pc.dim("-")}  Mesh: ${meshEnabled ? pc.green("enabled") : pc.red("disabled")}`);
    console.error(`  ${pc.dim("-")}  URL: ${config.meshUrl ?? pc.dim("none")}`);
    console.error(`  ${pc.dim("-")}  Pending: ${pending}`);
    console.error(`  ${pc.dim("-")}  Synced: ${synced}`);
    console.error("");
  });

// ── status ───────────────────────────────────────────────────────────────────

program
  .command("status")
  .description("Show route table for a project")
  .argument("[project]", "Project name")
  .action((project?: string) => {
    const config = loadConfig();
    const dbPath = join(getDataDir(), "openplan.db");
    if (!existsSync(dbPath)) {
      console.error(`  ${pc.dim("-")}  No data found.`);
      return;
    }
    const db = openDatabase(dbPath);
    const store = createStore(db, config.identityId);
    const proj = project ?? process.env.OPENPLAN_PROJECT ?? "default";
    const routes = store.getRoutesForProject(proj);

    if (program.opts().json) {
      console.log(JSON.stringify(routes, null, 2));
    } else {
      for (const r of routes) {
        const statusColor = r.status === "active" ? pc.green : r.status === "completed" ? pc.blue : pc.dim;
        console.error(
          `  ${statusColor(r.status.toUpperCase())} ${pc.dim(r.id.slice(0, 8))}  ${r.goal}  ${pc.dim(`(${r.totalExpected ?? "?"} / ${r.totalActual ?? "?"}s)`)}`,
        );
      }
      if (routes.length === 0) {
        console.error(`  ${pc.dim("-")}  No routes found for "${proj}".`);
      }
    }
  });

// ── log ──────────────────────────────────────────────────────────────────────

program
  .command("log")
  .description("Show checkpoint trail for a route or project")
  .argument("[route-or-project]", "Route ID or project name")
  .action((routeOrProject?: string) => {
    const config = loadConfig();
    const dbPath = join(getDataDir(), "openplan.db");
    if (!existsSync(dbPath)) {
      console.error(`  ${pc.dim("-")}  No data found.`);
      return;
    }
    const db = openDatabase(dbPath);
    const store = createStore(db, config.identityId);
    const events = store.getCalibrationEvents();

    if (program.opts().json) {
      console.log(JSON.stringify(events, null, 2));
    } else {
      for (const e of events) {
        const outcomeColor = e.outcome === "completed" ? pc.green : pc.yellow;
        console.error(
          `  ${pc.dim(e.createdAt.slice(0, 19))}  ${outcomeColor(e.outcome)}  ${e.action}  ${e.actualCost}s ${pc.dim(`(expected ${e.expectedCost}s)`)}`,
        );
      }
      if (events.length === 0) {
        console.error(`  ${pc.dim("-")}  No calibration events found.`);
      }
    }
  });

// ── export ──────────────────────────────────────────────────────────────────

program
  .command("export")
  .description("Export your calibration data (Pro)")
  .option("--format <type>", "Output format: json (default), csv, markdown")
  .option("--project <name>", "Filter by project")
  .action(async (options: { format: string; project: string }) => {
    const config = loadConfig();
    const base = meshUrl();
    const fmt = options.format || "json";

    if (!config.apiKey) {
      // Local-only export
      const dbPath = join(getDataDir(), "openplan.db");
      if (!existsSync(dbPath)) {
        console.error(`  ${pc.dim("-")}  No data found.`);
        return;
      }
      const db = openDatabase(dbPath);
      const store = createStore(db, config.identityId);
      const routes = options.project
        ? store.getRoutesForProject(options.project)
        : store
            .getCalibrationEvents()
            .reduce((acc: string[], e) => {
              if (e.routeId && !acc.includes(e.routeId)) acc.push(e.routeId);
              return acc;
            }, [])
            .map((id) => store.getRoute(id))
            .filter(Boolean);
      if (fmt === "json") {
        const data = {
          exported_at: new Date().toISOString(),
          routes: options.project
            ? store.getRoutesForProject(options.project).map((r) => ({
                project: r.project,
                goal: r.goal,
                status: r.status,
                phases: store.getPhases(r.id),
              }))
            : [],
          calibrations: store.getCalibrationEvents(),
        };
        console.log(JSON.stringify(data, null, 2));
      } else {
        console.error(
          `  ${pc.yellow("!")} Local export supports --format json only. Connect to Mesh for CSV/Markdown.\n`,
        );
      }
      return;
    }

    // Mesh-backed export (cross-machine)
    try {
      const resp = await fetch(`${base}/v1/export`, {
        headers: { Authorization: `Bearer ${config.apiKey}` },
      });
      if (!resp.ok) {
        const err = (await resp.json().catch(() => null)) as Record<string, unknown> | null;
        const detail = (err?.detail as string) ?? `HTTP ${resp.status}`;
        console.error(`  ${pc.red("!")} Export failed: ${detail}\n`);
        return;
      }
      const data = (await resp.json()) as Record<string, unknown>;

      if (fmt === "csv") {
        const calibrations = data.calibrations as Record<string, unknown>[];
        console.log("action,expected_cost,actual_cost,outcome,session_id,created_at");
        for (const c of calibrations) {
          console.log(
            `${c.action},${c.expected_cost ?? ""},${c.actual_cost},${c.outcome},${c.session_id ?? ""},${c.created_at ?? ""}`,
          );
        }
      } else if (fmt === "markdown") {
        const summary = data.summary as Record<string, unknown>;
        const calibrations = data.calibrations as Record<string, unknown>[];
        console.log("# OpenPlan Data Export\n");
        console.log(`Exported at: ${new Date((data.exported_at as number) * 1000).toISOString()}`);
        console.log(`Tier: ${data.tier}`);
        console.log("\n## Summary\n");
        console.log(`- Total calibrations: ${summary.total_calibrations}`);
        console.log("\n### Accuracy by Action\n");
        console.log("| Action | Samples | Mean Deviation | MAPE |");
        console.log("|--------|---------|---------------|------|");
        const accuracy = summary.accuracy_by_action as Record<string, unknown>[];
        for (const a of accuracy) {
          console.log(`| ${a.action} | ${a.sample_count} | ${a.mean_deviation ?? "-"} | ${a.mape ?? "-"}% |`);
        }
        console.log("\n## Calibration Events\n");
        for (const c of calibrations) {
          console.log(
            `- ${c.created_at} | ${c.action} | actual: ${c.actual_cost} | expected: ${c.expected_cost ?? "?"} | ${c.outcome}`,
          );
        }
      } else {
        console.log(JSON.stringify(data, null, 2));
      }
    } catch (e) {
      console.error(`  ${pc.red("!")} Export failed: ${e instanceof Error ? e.message : "unknown error"}\n`);
    }
  });

// ── Startup logic ────────────────────────────────────────────────────────────

const knownCommands = program.commands.map((c) => c.name());
const userArgs = process.argv.slice(2);
const firstNonFlag = userArgs.find((a) => !a.startsWith("-"));
const isKnownCommand = firstNonFlag !== undefined && knownCommands.includes(firstNonFlag);
const isHelp = userArgs.length === 1 && (userArgs[0] === "--help" || userArgs[0] === "-h");
const isVersion = userArgs.length === 1 && (userArgs[0] === "--version" || userArgs[0] === "-V");

if (isHelp) {
  program.outputHelp();
  process.exit(0);
} else if (isVersion) {
  console.log(pkg.version);
  process.exit(0);
} else if (firstNonFlag === "help") {
  const helpCmd = userArgs[1] ? program.commands.find((c) => c.name() === userArgs[1]) : null;
  console.log(helpCmd ? helpCmd.helpInformation() : program.helpInformation());
  process.exit(0);
} else if (isKnownCommand) {
  program.parse(process.argv);
} else {
  startServer().catch((e) => {
    console.error(
      `${pc.red("!")} Failed to start OpenPlan MCP server: ${e instanceof Error ? e.message : "unknown error"}`,
    );
    process.exit(1);
  });
}
