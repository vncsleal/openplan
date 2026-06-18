#!/usr/bin/env node
import { Command } from "commander";
import { loadConfig, saveConfig, getConfigPath, ensureConfig, type AppConfig } from "./config.js";
import { createServer } from "./server.js";

const program = new Command();

program
  .name("openplan")
  .description("Waze for AI agents — plan, track, and learn from software projects")
  .version("0.1.0");

// Default: start MCP server
program.action(() => {
  const server = createServer();
  server.start({ transportType: "stdio" });
});

// install
program
  .command("install")
  .description("Detect MCP clients and add OpenPlan to them")
  .action(async () => {
    ensureConfig();
    const { intro, outro, select, isCancel } = await import("@clack/prompts");
    const picocolors = await import("picocolors");
    const pc = picocolors.default;

    intro(pc.bold("OpenPlan Install"));

    const clients: Array<{ name: string; path: string }> = [];
    const { existsSync } = await import("node:fs");
    const home = process.env.HOME || "";

    if (existsSync(`${home}/.config/opencode/opencode.json`)) {
      clients.push({ name: "OpenCode", path: `${home}/.config/opencode/opencode.json` });
    }
    const claudePath = `${home}/Library/Application Support/Claude/claude_desktop_config.json`;
    if (existsSync(claudePath)) {
      clients.push({ name: "Claude Desktop", path: claudePath });
    }

    if (clients.length === 0) {
      console.log(pc.yellow("No supported MCP clients detected."));
      console.log(`Config created at ${pc.cyan(getConfigPath())}`);
      outro(pc.green("OpenPlan is ready."));
      return;
    }

    const selected = await select({
      message: "Add OpenPlan to which client?",
      options: clients.map(c => ({ label: c.name, value: c.path })),
    });

    if (isCancel(selected)) {
      outro(pc.red("Install cancelled."));
      return;
    }

    const { readFileSync, writeFileSync } = await import("node:fs");
    let clientConfig: Record<string, unknown>;
    try {
      clientConfig = JSON.parse(readFileSync(selected as string, "utf-8"));
    } catch {
      clientConfig = {};
    }
    if (!clientConfig.mcpServers) {
      clientConfig.mcpServers = {};
    }
    (clientConfig.mcpServers as Record<string, unknown>).openplan = {
      type: "local",
      command: ["npx", "-y", "@openplan/mcp"],
      enabled: true,
      environment: {
        OPENPLAN_API_KEY: process.env.OPENPLAN_API_KEY || "",
        OPENCODE_SESSION_ID: "{env:OPENCODE_SESSION_ID}",
      },
    };
    writeFileSync(selected as string, JSON.stringify(clientConfig, null, 2), "utf-8");
    console.log(pc.green(`✔ MCP entry added to ${selected}`));
    console.log(`Config created at ${pc.cyan(getConfigPath())}`);
    outro(pc.green("OpenPlan is ready."));
  });

// auth
program
  .command("auth")
  .description("Authenticate with GitHub for Mesh access")
  .action(async () => {
    const config = loadConfig();
    const picocolors = await import("picocolors");
    const pc = picocolors.default;
    const meshUrl = config.mesh.apiUrl;

    console.log(pc.bold(`\nAuthenticating with ${meshUrl}...`));

    try {
      // Step 1: Initiate device code flow
      const initResp = await fetch(`${meshUrl}/v1/auth/device`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });

      if (!initResp.ok) {
        console.error(pc.red(`Auth server returned ${initResp.status}`));
        process.exit(1);
      }

      const { device_code, user_code, verification_uri, interval } = await initResp.json() as {
        device_code: string;
        user_code: string;
        verification_uri: string;
        interval: number;
      };

      console.log(`\n  ${pc.bold("Open this URL:")} ${pc.cyan(verification_uri)}`);
      console.log(`  ${pc.bold("Enter code:")}   ${pc.yellow(user_code)}`);
      console.log();

      // Try to open browser
      const { execSync } = await import("node:child_process");
      try {
        execSync(`open "${verification_uri}"`, { timeout: 3000 });
        console.log(pc.dim("  (Browser opened automatically)"));
      } catch {
        console.log(pc.dim("  (Open the URL manually)"));
      }

      // Step 2: Poll for token
      const pollInterval = Math.max(interval, 5) * 1000;
      let apiKey = "";

      for (let attempts = 0; attempts < 60; attempts++) {
        await new Promise(r => setTimeout(r, pollInterval));
        try {
          const pollResp = await fetch(`${meshUrl}/v1/auth/device/poll`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ device_code }),
          });

          if (pollResp.status === 200) {
            const data = await pollResp.json() as { api_key: string };
            apiKey = data.api_key;
            break;
          }
          if (pollResp.status === 401) {
            continue;
          }
          console.error(pc.red(`Poll error: ${pollResp.status}`));
          process.exit(1);
        } catch {
          // Network error, retry
        }
      }

      if (!apiKey) {
        console.error(pc.red("Authentication timed out."));
        process.exit(1);
      }

      // Step 3: Save API key
      config.mesh.apiKey = apiKey;
      saveConfig(config);

      console.log(pc.green(`\n✔ Authenticated. API key saved to ${getConfigPath()}`));

      // Print account info
      try {
        const acctResp = await fetch(`${meshUrl}/v1/account`, {
          headers: { Authorization: `Bearer ${apiKey}` },
        });
        if (acctResp.ok) {
          const acct = await acctResp.json() as { tier?: string; checkpoint_count?: number };
          console.log(`  Tier: ${pc.bold(acct.tier || "Free")}`);
          if (acct.checkpoint_count !== undefined) {
            console.log(`  Global checkpoints: ${acct.checkpoint_count}`);
          }
        }
      } catch {
        // account info is best-effort
      }
    } catch (err) {
      console.error(pc.red(`Auth failed: ${(err as Error).message}`));
      process.exit(1);
    }
  });

// subscribe
program
  .command("subscribe")
  .description("Upgrade to Pro via Stripe")
  .action(async () => {
    const config = loadConfig();
    const picocolors = await import("picocolors");
    const pc = picocolors.default;

    if (!config.mesh.apiKey) {
      console.error(pc.red("Not authenticated. Run `openplan auth` first."));
      process.exit(1);
    }

    console.log(pc.bold("\nCreating Stripe Checkout Session..."));

    try {
      const resp = await fetch(`${config.mesh.apiUrl}/v1/subscribe`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${config.mesh.apiKey}`,
        },
      });

      if (!resp.ok) {
        console.error(pc.red(`Subscribe failed: ${resp.status}`));
        process.exit(1);
      }

      const { checkout_url } = await resp.json() as { checkout_url: string };

      console.log(`\n  ${pc.bold("Checkout URL:")} ${pc.cyan(checkout_url)}`);
      console.log();

      const { execSync } = await import("node:child_process");
      try {
        execSync(`open "${checkout_url}"`, { timeout: 3000 });
        console.log(pc.dim("  (Browser opened automatically)"));
      } catch {
        console.log(pc.dim("  (Open the URL manually)"));
      }
    } catch (err) {
      console.error(pc.red(`Subscribe failed: ${(err as Error).message}`));
      process.exit(1);
    }
  });

// account
program
  .command("account")
  .description("Account info, plan, checkpoint count")
  .action(async () => {
    const config = loadConfig();
    const picocolors = await import("picocolors");
    const pc = picocolors.default;
    const { getConnection } = await import("./db/connection.js");
    const conn = getConnection(config.core.dbPath);

    const total = conn.$client.prepare("SELECT COUNT(*) as cnt FROM calibration_events").get() as { cnt: number };
    const bias = conn.$client.prepare("SELECT AVG(actual_cost / expected_cost) as b FROM calibration_events WHERE expected_cost > 0").get() as { b: number | null };
    const routesCount = conn.$client.prepare("SELECT COUNT(*) as cnt FROM routes").get() as { cnt: number };
    const archived = conn.$client.prepare("SELECT COUNT(*) as cnt FROM routes WHERE archived = 1").get() as { cnt: number };

    console.log(pc.bold("\nOpenPlan Account"));
    console.log(`  Plan:         ${config.mesh.apiKey ? pc.green("Free") : pc.yellow("Free (no API key)")}`);
    console.log(`  API key:      ${config.mesh.apiKey ? pc.green("configured") : pc.dim("not set")}`);
    console.log(`  Checkpoints:  ${total.cnt}`);
    console.log(`  Routes:       ${routesCount.cnt} (${archived.cnt} archived)`);
    console.log(`  Personal bias: ${bias.b !== null ? (bias.b * 100).toFixed(0) + "%" : pc.dim("N/A (need 3+ checkpoints)")}`);
    console.log(`  Mesh:         ${config.mesh.apiUrl}`);

    if (config.mesh.apiKey) {
      try {
        const resp = await fetch(`${config.mesh.apiUrl}/v1/account`, {
          headers: { Authorization: `Bearer ${config.mesh.apiKey}` },
        });
        if (resp.ok) {
          const data = await resp.json() as { tier?: string; checkpoint_count?: number; bias?: number };
          console.log(`  Mesh tier:    ${pc.cyan(data.tier || "Free")}`);
          if (data.checkpoint_count !== undefined) console.log(`  Mesh checkpoints: ${data.checkpoint_count}`);
        }
      } catch {
        console.log(`  Mesh:         ${pc.yellow("unreachable")}`);
      }
    }
    console.log();
  });

// config
program
  .command("config")
  .description("Show configuration")
  .argument("[action]", "Action: show")
  .action(async (action?: string) => {
    if (action !== "show") {
      console.log("Usage: openplan config show");
      return;
    }

    const config = loadConfig();
    const picocolors = await import("picocolors");
    const pc = picocolors.default;

    console.log(pc.bold("\nOpenPlan Config"));
    console.log(`  Config path:  ${pc.cyan(getConfigPath())}`);
    console.log(`  DB path:      ${config.core.dbPath}`);
    console.log(`  Mesh URL:     ${config.mesh.apiUrl}`);
    console.log(`  API key:      ${config.mesh.apiKey ? pc.green("*** configured ***") : pc.dim("not set")}`);
    console.log(`  Cost probe:   ${config.costProbe?.command ? pc.green(config.costProbe.command) : pc.dim("none")}`);
    console.log();
  });

// status
program
  .command("status")
  .description("Show current route state")
  .argument("[project]", "Project name (derives from CWD if omitted)")
  .action(async (project?: string) => {
    const config = loadConfig();
    const picocolors = await import("picocolors");
    const pc = picocolors.default;
    const { getConnection } = await import("./db/connection.js");
    const conn = getConnection(config.core.dbPath);

    const row = conn.$client.prepare(
      project
        ? "SELECT id, project, goal, status FROM routes WHERE project = ? AND archived = 0 ORDER BY created_at DESC LIMIT 1"
        : "SELECT id, project, goal, status FROM routes WHERE archived = 0 ORDER BY created_at DESC LIMIT 1"
    ).get(project ?? undefined) as Record<string, unknown> | undefined;

    if (!row) {
      console.log(pc.yellow("\nNo active route. Call plan() to start a new project.\n"));
      return;
    }

    const phases = conn.$client.prepare(
      "SELECT label, action, expected_cost, actual_cost, outcome, status, sequence FROM route_phases WHERE route_id = ? ORDER BY sequence"
    ).all(row.id as string) as Array<{
      label: string; action: string; expected_cost: number; actual_cost: number | null;
      outcome: string | null; status: string; sequence: number;
    }>;

    const doneCount = phases.filter(p => p.status === "done").length;

    console.log(pc.bold(`\n${row.project}`) + pc.dim(` · ${row.goal} · ${row.status}`));

    for (const p of phases) {
      const icon = p.status === "done" ? pc.green("✅") : p.status === "in_progress" ? pc.yellow("🔄") : pc.dim("⏳");
      const cost = p.actual_cost !== null
        ? pc.green(` → ${p.actual_cost}`)
        : pc.dim(` (expected ${Math.round(p.expected_cost)})`);
      console.log(`  ${icon} ${p.label}${cost}`);
    }

    // Archived routes
    const archived = conn.$client.prepare(
      "SELECT id, project, goal, abandon_reason FROM routes WHERE project = ? AND archived = 1 ORDER BY created_at DESC LIMIT 3"
    ).all(row.project as string) as Array<{ id: string; project: string; goal: string; abandon_reason: string | null }>;

    if (archived.length > 0) {
      console.log(pc.dim(`\nArchived (${archived.length}):`));
      for (const a of archived) {
        console.log(pc.dim(`  ${a.id} — ${a.abandon_reason || "abandoned"}`));
      }
    }

    console.log(pc.dim(`\nPosition: ${doneCount}/${phases.length} phases`));
    console.log();
  });

// log
program
  .command("log")
  .description("Show checkpoint history")
  .argument("[identifier]", "Route ID or project name")
  .option("--json", "Output as JSON")
  .action(async (identifier?: string) => {
    const config = loadConfig();
    const picocolors = await import("picocolors");
    const pc = picocolors.default;
    const { getConnection } = await import("./db/connection.js");
    const conn = getConnection(config.core.dbPath);

    let events: Array<{
      created_at: string; action: string; expected_cost: number; actual_cost: number;
      outcome: string; phase_label_tokens: string;
    }>;

    if (identifier) {
      events = conn.$client.prepare(`
        SELECT * FROM calibration_events
        WHERE project IN (SELECT project FROM routes WHERE id = ?)
        ORDER BY created_at DESC LIMIT 50
      `).all(identifier) as typeof events;

      if (events.length === 0) {
        events = conn.$client.prepare(
          "SELECT * FROM calibration_events WHERE project = ? ORDER BY created_at DESC LIMIT 50"
        ).all(identifier) as typeof events;
      }
    } else {
      events = conn.$client.prepare(
        "SELECT * FROM calibration_events ORDER BY created_at DESC LIMIT 50"
      ).all() as typeof events;
    }

    if (events.length === 0) {
      console.log(pc.yellow("\nNo checkpoints recorded.\n"));
      return;
    }

    if (process.argv.includes("--json")) {
      console.log(JSON.stringify(events, null, 2));
      return;
    }

    console.log(pc.bold(`\nCheckpoints (${events.length}):`));
    for (const e of events) {
      const ratio = e.expected_cost > 0 ? (e.actual_cost / e.expected_cost) : 1;
      const dev = ratio <= 1.3 ? pc.green(`${(ratio * 100).toFixed(0)}%`) : ratio <= 2.0 ? pc.yellow(`${(ratio * 100).toFixed(0)}%`) : pc.red(`${(ratio * 100).toFixed(0)}%`);
      const date = new Date(e.created_at).toLocaleDateString();
      console.log(`  ${pc.dim(date)} ${e.action.padEnd(15)} ${pc.dim(`expected ${Math.round(e.expected_cost)} → actual ${Math.round(e.actual_cost)}`)} ${dev} ${e.outcome}`);
    }
    console.log();
  });

export function main(): void {
  program.parse(process.argv);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main();
}
