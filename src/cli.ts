#!/usr/bin/env node
process.env.NO_COLOR = process.env.NO_COLOR ?? "";
import { Command } from "commander";
import { loadConfig, saveConfig, getConfigPath, getDataDir, ensureDirectories } from "./config.js";
import { startServer } from "./server.js";
import { openDatabase } from "./db/connection.js";
import { createStore } from "./db/store.js";
import { join } from "node:path";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import pc from "picocolors";

const program = new Command();

program
  .name("openplan")
  .description("Waze for AI agents -- plan, track, and learn from software projects")
  .version("0.1.0")
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

program
  .command("auth")
  .description("Authenticate with OpenPlan Mesh (GitHub OAuth)")
  .action(async () => {
    console.error(pc.yellow("Authentication is not yet available in v0.1.0."));
    console.error("Set OPENPLAN_API_KEY environment variable or add api_key to config.");
  });

program
  .command("subscribe")
  .description("Manage subscription (Stripe Checkout)")
  .action(() => {
    console.error(pc.yellow("Subscriptions are not yet available in v0.1.0."));
    console.error("Visit https://openplan.cc to learn more.");
  });

program
  .command("account")
  .description("Account info, export/delete data")
  .action(() => {
    const config = loadConfig();
    if (program.opts().json) {
      console.log(JSON.stringify({ identityId: config.identityId, dataDir: config.dataDir }, null, 2));
    } else {
      console.error(pc.cyan(`Identity: ${config.identityId}`));
      console.error(pc.cyan(`Data: ${config.dataDir}`));
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

if (isKnownCommand) {
  program.parse(process.argv);
} else {
  startServer().catch((e) => {
    console.error(pc.red("Failed to start OpenPlan MCP server:"), e);
    process.exit(1);
  });
}
