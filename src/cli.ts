#!/usr/bin/env node
process.env.NO_COLOR = process.env.NO_COLOR ?? "";
import { Command } from "commander";
import { loadConfig, saveConfig, getConfigPath, getDataDir, ensureDirectories } from "./config.js";
import { startServer } from "./server.js";
import { openDatabase } from "./db/connection.js";
import { createStore } from "./db/store.js";
import { join, dirname } from "node:path";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import pc from "picocolors";

const __dirname = dirname(fileURLToPath(import.meta.url));
const pkg = JSON.parse(readFileSync(join(__dirname, "..", "package.json"), "utf-8")) as { version: string };

const program = new Command();

program
  .name("openplan")
  .description("Waze for AI agents -- plan, track, and learn from software projects")
  .version(pkg.version)
  .option("--json", "Output in JSON format")
  .option("--no-color", "Disable color output");

program
  .command("install")
  .description("Detect MCP clients and install OpenPlan")
  .action(async () => {
    const { confirm, isCancel } = await import("@clack/prompts");

    const openplanEntry = {
      command: "npx",
      args: ["-y", "@openplan/mcp"],
    };
    const openplanLocal = {
      type: "local",
      command: ["npx", "-y", "@openplan/mcp"],
      enabled: true,
    };

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
      console.error(pc.red("No supported MCP clients detected."));
      console.error("Install OpenCode or Claude Desktop, then run `openplan install` again.");
      process.exit(1);
    }

    const detected: string[] = [];
    if (hasOpencode) detected.push("OpenCode");
    if (hasClaude) detected.push("Claude Desktop");
    console.error(pc.cyan(`Detected MCP clients: ${detected.join(", ")}`));

    const shouldInstall = await confirm({
      message: `Install OpenPlan in ${detected.join(" and ")}?`,
    });

    if (isCancel(shouldInstall) || !shouldInstall) {
      console.error(pc.yellow("Installation cancelled."));
      process.exit(0);
    }

    if (hasOpencode) {
      try {
        const raw = readFileSync(opencodeConfig, "utf-8");
        const cfg = JSON.parse(raw);
        if (!cfg.mcp) cfg.mcp = {};
        if (!cfg.mcp.openplan) {
          cfg.mcp.openplan = openplanLocal;
          writeFileSync(opencodeConfig, JSON.stringify(cfg, null, 2), "utf-8");
          console.error(pc.green("✓ Installed in OpenCode"));
        } else {
          console.error(pc.yellow("→ OpenPlan already configured in OpenCode"));
        }
      } catch (e) {
        console.error(pc.red(`Failed to update OpenCode config: ${e instanceof Error ? e.message : "unknown error"}`));
      }
    }

    if (hasClaude) {
      try {
        const raw = readFileSync(claudeConfig, "utf-8");
        const cfg = JSON.parse(raw);
        if (!cfg.mcpServers) cfg.mcpServers = {};
        if (!cfg.mcpServers.openplan) {
          cfg.mcpServers.openplan = openplanEntry;
          writeFileSync(claudeConfig, JSON.stringify(cfg, null, 2), "utf-8");
          console.error(pc.green("✓ Installed in Claude Desktop"));
        } else {
          console.error(pc.yellow("→ OpenPlan already configured in Claude Desktop"));
        }
      } catch (e) {
        console.error(pc.red(`Failed to update Claude config: ${e instanceof Error ? e.message : "unknown error"}`));
      }
    }

    console.error(pc.green("\nOpenPlan is ready. Restart your MCP client to start using it."));
  });

function meshUrl(): string {
  return process.env.OPENPLAN_MESH_URL ?? "https://api.openplan.cc";
}

program
  .command("auth")
  .description("Authenticate with OpenPlan Mesh (GitHub OAuth)")
  .option("--no-browser", "Do not open browser automatically")
  .option("--clipboard", "Copy code to clipboard")
  .action(async (options: { browser: boolean; clipboard: boolean }) => {
    const base = meshUrl();
    const isInteractive = process.stdout.isTTY && !process.env.CI;
    const s = isInteractive ? (await import("@clack/prompts")).spinner() : null;

    process.on("SIGINT", () => {
      s?.stop("Authentication cancelled");
      process.exit(0);
    });

    try {
      const deviceResp = await fetch(`${base}/v1/auth/device`, { method: "POST" });
      if (!deviceResp.ok) throw new Error(`Device auth failed (${deviceResp.status})`);
      const device = (await deviceResp.json()) as Record<string, unknown>;

      const userCode = device.user_code as string;
      const verificationUri = (device.verification_uri as string) ?? "https://github.com/login/device";
      let interval = (device.interval as number) ?? 5;
      const deviceCode = device.device_code as string;
      const expiresIn = (device.expires_in as number) ?? 900;

      // ── Display ──────────────────────────────────────────────
      console.error("");
      console.error(`  ${pc.cyan("○")}  ${pc.bold("OpenPlan Mesh Authentication")}`);
      console.error("");
      console.error(`  ${pc.dim("→")}  Open this URL in your browser:`);
      console.error(`     ${pc.cyan(verificationUri)}`);
      console.error("");
      console.error(`  ${pc.dim("→")}  Then enter the code:  ${pc.bold(pc.bgGreen(pc.black(` ${userCode} `)))}`);
      console.error(`     ${pc.dim(`Expires in ${Math.floor(expiresIn / 60)} minutes`)}`);
      console.error("");

      // ── Clipboard ────────────────────────────────────────────
      if (options.clipboard) {
        try {
          const { execSync } = await import("node:child_process");
          const cmd =
            process.platform === "darwin"
              ? `echo "${userCode}" | pbcopy`
              : `echo "${userCode}" | xclip -selection clipboard`;
          execSync(cmd, { timeout: 2000 });
          console.error(`  ${pc.dim("→")} Code copied to clipboard`);
          console.error("");
        } catch {
          // clipboard unavailable on this platform
        }
      }

      // ── Auto-open browser ────────────────────────────────────
      if (options.browser && isInteractive) {
        try {
          const { execSync } = await import("node:child_process");
          execSync(`open "${verificationUri}"`, { timeout: 3000 });
          console.error(`  ${pc.dim("→")} Browser opened`);
          console.error("");
        } catch {
          // Fallback: user opens manually
        }
      }

      // ── Poll ─────────────────────────────────────────────────
      s?.start("Waiting for GitHub authentication");

      const maxAttempts = Math.ceil(expiresIn / interval);
      for (let i = 0; i < maxAttempts; i++) {
        await new Promise((r) => setTimeout(r, interval * 1000));

        const pollResp = await fetch(`${base}/v1/auth/device/poll`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ device_code: deviceCode }),
        });
        if (!pollResp.ok) continue;
        const poll = (await pollResp.json()) as Record<string, unknown>;

        if (poll.access_token) {
          s?.stop("GitHub authentication complete");

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
          const apiKey = keyData.api_key as string;

          saveConfig({ apiKey, meshUrl: base });
          console.error(`  ${pc.green("✓")} Authenticated! API key saved to config.\n`);
          return;
        }

        if (poll.error === "authorization_pending") continue;
        if (poll.error === "slow_down") {
          interval += 5;
          continue;
        }
        if (poll.error === "expired_token") {
          s?.stop("Session expired");
          console.error(`  ${pc.red("✗")} Session expired. Run \`openplan auth\` again.\n`);
          return;
        }
        if (poll.error === "access_denied") {
          s?.stop("Authorization denied");
          console.error(`  ${pc.red("✗")} Authorization was denied.\n`);
          return;
        }
      }

      s?.stop("Timed out");
      console.error(`  ${pc.red("✗")} Timed out. Run \`openplan auth\` again.\n`);
    } catch (e) {
      s?.stop("Auth failed");
      console.error(`  ${pc.red("✗")} ${e instanceof Error ? e.message : "unknown error"}\n`);
    }
  });

program
  .command("subscribe")
  .description("Manage subscription (Stripe Checkout)")
  .argument("[plan]", "Plan: pro (default) or enterprise", "pro")
  .action(async (plan: string) => {
    const config = loadConfig();
    if (!config.apiKey) {
      console.error(pc.yellow("Not authenticated. Run `openplan auth` first."));
      process.exit(1);
    }

    const base = meshUrl();
    try {
      const resp = await fetch(`${base}/v1/subscribe`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan, api_key: config.apiKey }),
      });
      if (!resp.ok) {
        const err = (await resp.json().catch(() => null)) as Record<string, unknown> | null;
        console.error(pc.red(err?.detail ? `Subscribe failed: ${err.detail}` : `Subscribe failed (${resp.status})`));
        return;
      }
      const data = (await resp.json()) as Record<string, unknown>;
      const url = data.checkout_url as string;
      console.error(pc.bold("\nOpenPlan Pro Subscription\n"));
      console.error(`Complete checkout at: ${pc.cyan(url)}`);
      console.error("Your subscription activates automatically after payment.\n");
    } catch (e) {
      console.error(pc.red(`Subscribe failed: ${e instanceof Error ? e.message : "unknown error"}`));
    }
  });

program
  .command("account")
  .description("Account info, export/delete data")
  .action(async () => {
    const config = loadConfig();
    const base = meshUrl();

    let subStatus: Record<string, unknown> | null = null;
    if (config.apiKey) {
      try {
        const resp = await fetch(`${base}/v1/account`, {
          headers: { Authorization: `Bearer ${config.apiKey}` },
        });
        if (resp.ok) subStatus = (await resp.json()) as Record<string, unknown>;
      } catch {
        // Mesh unreachable — show local-only info
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
      console.error(pc.cyan(`Identity: ${config.identityId}`));
      console.error(pc.cyan(`Data: ${config.dataDir}`));
      console.error(`API Key: ${config.apiKey ? pc.green("configured") : pc.dim("not configured")}`);
      if (subStatus) {
        console.error(
          `Subscription: ${pc.green((subStatus.tier as string) ?? "free")} — ${subStatus.status as string}`,
        );
      } else {
        console.error(`Subscription: ${pc.dim("free (unauthenticated)")}`);
      }
    }
  });

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
        console.error(pc.bold(`Config file: ${getConfigPath()}`));
        console.error(pc.bold(`Data directory: ${getDataDir()}`));
        console.error(`Identity: ${config.identityId}`);
        console.error(`Mesh URL: ${config.meshUrl ?? pc.dim("not configured")}`);
        console.error(`API Key: ${config.apiKey ? pc.green("configured") : pc.dim("not configured")}`);
        console.error(`Cost Probe: ${config.costProbeCommand ?? pc.dim("not configured")}`);
      }
    }
  });

program
  .command("status")
  .description("Show route table for a project")
  .argument("[project]", "Project name")
  .action((project?: string) => {
    const config = loadConfig();
    const dbPath = join(getDataDir(), "openplan.db");
    if (!existsSync(dbPath)) {
      console.error(pc.dim("No data found. Start by running a plan."));
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
          `${statusColor(r.status.toUpperCase())} ${pc.dim(r.id.slice(0, 8))}: ${r.goal} (expected: ${r.totalExpected ?? "?"}, actual: ${r.totalActual ?? "?"})`,
        );
      }
      if (routes.length === 0) {
        console.error(pc.dim("No routes found for this project."));
      }
    }
  });

program
  .command("log")
  .description("Show checkpoint trail for a route or project")
  .argument("[route-or-project]", "Route ID or project name")
  .action((routeOrProject?: string) => {
    const config = loadConfig();
    const dbPath = join(getDataDir(), "openplan.db");
    if (!existsSync(dbPath)) {
      console.error(pc.dim("No data found."));
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
          `${pc.dim(e.createdAt.slice(0, 19))} ${outcomeColor(`[${e.outcome}]`)} ${e.action}: ${e.actualCost}s (expected ${e.expectedCost}s)`,
        );
      }
      if (events.length === 0) {
        console.error(pc.dim("No calibration events found."));
      }
    }
  });

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
  // Handle `help [command]` ourselves — Commander's handler doesn't
  // flush stdout before process.exit in piped environments
  const helpCmd = userArgs[1] ? program.commands.find((c) => c.name() === userArgs[1]) : null;
  console.log(helpCmd ? helpCmd.helpInformation() : program.helpInformation());
  process.exit(0);
} else if (isKnownCommand) {
  program.parse(process.argv);
} else {
  startServer().catch((e) => {
    console.error(pc.red("Failed to start OpenPlan MCP server:"), e);
    process.exit(1);
  });
}
