#!/usr/bin/env node
import { existsSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Command } from "commander";
import pc from "picocolors";
import { parse, stringify } from "smol-toml";
import { DEFAULT_MESH_URL, ensureDirectories, getConfigPath, getDataDir, loadConfig, saveConfig } from "./config.js";
import { createLogger } from "./core/logger.js";
import {
  AccountResponse,
  ApiKeyResponse,
  DeviceAuthResponse,
  ErrorDetailResponse,
  ExportResponse,
  PollAuthResponse,
  PortalResponse,
  SubscribeResponse,
} from "./core/schemas.js";
import { openDatabase } from "./db/connection.js";
import { createStore } from "./db/store.js";
import { startServer } from "./server.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const pkg = JSON.parse(readFileSync(join(__dirname, "..", "package.json"), "utf-8")) as { version: string };
const log = createLogger("cli");

const COMMAND_CATEGORIES: Record<string, string[]> = {
  "Authentication:": ["auth", "subscribe", "portal", "account"],
  "Data:": ["config", "status", "log", "export"],
  "Admin:": ["install", "uninstall", "doctor", "completion", "mesh"],
  "Info:": ["help", "--version"],
};

const program = new Command();

program
  .name("openplan")
  .description("Waze for AI agents -- plan, track, and learn from software projects")
  .version(pkg.version)
  .option("--json", "Output in JSON format")
  .option("--no-color", "Disable color output")
  .configureHelp({
    formatHelp: (cmd, helper) => {
      const lines: string[] = [];
      lines.push(`Usage: ${cmd.name()} [options] [command]\n`);
      lines.push("Waze for AI agents -- plan, track, and learn from software projects\n");

      const opts = cmd.options.filter((o) => !o.hidden);
      if (opts.length > 0) {
        lines.push("Options:");
        for (const o of opts) {
          lines.push(`  ${o.flags.padEnd(20)} ${o.description ?? ""}`);
        }
        lines.push("");
      }

      lines.push("Commands:");
      for (const [category, cmds] of Object.entries(COMMAND_CATEGORIES)) {
        lines.push(`  ${category}`);
        for (const name of cmds) {
          const found = cmd.commands.find((c) => c.name() === name);
          if (found) {
            lines.push(`    ${name.padEnd(16)} ${found.description()}`);
          }
        }
      }

      lines.push("");
      lines.push("Examples:");
      lines.push("  openplan install              Detect and install in MCP clients");
      lines.push("  openplan auth                 Authenticate with GitHub");
      lines.push("  openplan config show          View configuration");
      lines.push("  openplan doctor               Check system health");
      lines.push("");
      return lines.join("\n");
    },
  });

function meshUrl(): string {
  return process.env.OPENPLAN_MESH_URL ?? DEFAULT_MESH_URL;
}

function isUUID(s: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s);
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
          process.exit(1);
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
          process.exit(1);
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
      if (!deviceResp.ok) {
        console.error(`  ${pc.red("!")} Device auth failed (${deviceResp.status}).\n`);
        process.exit(1);
      }
      const deviceRaw = await deviceResp.json();
      const device = DeviceAuthResponse.parse(deviceRaw);
      if (options.debug) console.error(`  ${pc.dim("[debug]")} device: ${JSON.stringify(device)}`);

      const userCode = device.user_code;
      const verificationUri = device.verification_uri;
      let interval = device.interval;
      const deviceCode = device.device_code;
      const expiresIn = device.expires_in;
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
          log.debug("Clipboard copy failed");
        }
      }

      if (options.browser && isInteractive) {
        try {
          const { execSync } = await import("node:child_process");
          execSync(`open "${verificationUri}"`, { timeout: 3000 });
          console.error(`  ${pc.dim(">")}  Browser opened`);
          console.error("");
        } catch {
          log.debug("Browser open failed, continuing with manual flow");
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

        const pollRaw = await pollResp.json();
        const poll = PollAuthResponse.parse(pollRaw);
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
          if (!keyResp.ok) {
            console.error(`  ${pc.red("!")} Failed to create API key.\n`);
            process.exit(1);
          }
          const keyRaw = await keyResp.json();
          const keyData = ApiKeyResponse.parse(keyRaw);
          if (options.debug) console.error(`  ${pc.dim("[debug]")} key: ${JSON.stringify(keyData)}`);

          saveConfig({ apiKey: keyData.api_key, meshUrl: base });
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
          console.error(`  ${pc.red("!")} Session expired. Run \`openplan auth\` again.\n`);
          return;
        }
        if (poll.error === "access_denied") {
          process.stderr.write("\r                                                          \r");
          console.error(`  ${pc.red("!")} Authorization denied.\n`);
          return;
        }

        process.stderr.write("\r                                                          \r");
        console.error(`  ${pc.red("!")} ${poll.error_description ?? poll.error ?? "Unknown error"}\n`);
        return;
      }

      process.stderr.write("\r                                                          \r");
      console.error(`  ${pc.red("!")} Timed out after ${expiryMinutes} minutes. Run \`openplan auth\` again.\n`);
    } catch (e) {
      console.error(`  ${pc.red("!")} ${e instanceof Error ? e.message : "unknown error"}\n`);
      process.exit(1);
    }
  });

// ── uninstall ───────────────────────────────────────────────────────────────

program
  .command("uninstall")
  .description("Remove OpenPlan from MCP clients and optionally delete local data")
  .option("--all", "Also delete local database and config")
  .action(async (options: { all: boolean }) => {
    const opencodeDir = process.env.XDG_CONFIG_HOME
      ? join(process.env.XDG_CONFIG_HOME, "opencode")
      : join(process.env.HOME ?? "/tmp", ".config", "opencode");
    const opencodeConfig = join(opencodeDir, "opencode.json");
    const claudeConfig = join(
      process.env.HOME ?? "/tmp",
      "Library",
      "Application Support",
      "Claude",
      "claude_desktop_config.json",
    );

    let removed = 0;

    if (existsSync(opencodeConfig)) {
      try {
        const raw = readFileSync(opencodeConfig, "utf-8");
        const cfg: Record<string, unknown> = JSON.parse(raw);
        const mcp = cfg.mcp as Record<string, unknown> | undefined;
        if (mcp?.openplan) {
          const { openplan: _, ...rest } = mcp;
          if (Object.keys(rest).length === 0) {
            const { mcp: _mcp, ...cfgRest } = cfg;
            writeFileSync(opencodeConfig, JSON.stringify(cfgRest, null, 2), "utf-8");
          } else {
            cfg.mcp = rest;
            writeFileSync(opencodeConfig, JSON.stringify(cfg, null, 2), "utf-8");
          }
          console.error(`  ${pc.green("*")} Removed from OpenCode`);
          removed++;
        }
      } catch (e) {
        console.error(
          `  ${pc.red("!")} Failed to update OpenCode: ${e instanceof Error ? e.message : "unknown error"}`,
        );
      }
    }

    if (existsSync(claudeConfig)) {
      try {
        const raw = readFileSync(claudeConfig, "utf-8");
        const cfg: Record<string, unknown> = JSON.parse(raw);
        const servers = cfg.mcpServers as Record<string, unknown> | undefined;
        if (servers?.openplan) {
          const { openplan: _, ...rest } = servers;
          if (Object.keys(rest).length === 0) {
            const { mcpServers: _ms, ...cfgRest } = cfg;
            writeFileSync(claudeConfig, JSON.stringify(cfgRest, null, 2), "utf-8");
          } else {
            cfg.mcpServers = rest;
            writeFileSync(claudeConfig, JSON.stringify(cfg, null, 2), "utf-8");
          }
          console.error(`  ${pc.green("*")} Removed from Claude Desktop`);
          removed++;
        }
      } catch (e) {
        console.error(
          `  ${pc.red("!")} Failed to update Claude Desktop: ${e instanceof Error ? e.message : "unknown error"}`,
        );
      }
    }

    if (removed === 0) {
      console.error(`  ${pc.yellow(">")} OpenPlan was not configured in any MCP client.`);
    }

    if (options.all) {
      const dbPath = join(getDataDir(), "openplan.db");
      if (existsSync(dbPath)) {
        try {
          rmSync(dbPath, { force: true });
          console.error(`  ${pc.green("*")} Local database deleted.`);
        } catch (e) {
          console.error(
            `  ${pc.red("!")} Failed to delete database: ${e instanceof Error ? e.message : "unknown error"}`,
          );
        }
      }
      const configPath = getConfigPath();
      if (existsSync(configPath)) {
        try {
          rmSync(configPath, { force: true });
          console.error(`  ${pc.green("*")} Config file deleted.`);
        } catch (e) {
          console.error(
            `  ${pc.red("!")} Failed to delete config: ${e instanceof Error ? e.message : "unknown error"}`,
          );
        }
      }
    }

    console.error(`\n  ${pc.green("*")} OpenPlan uninstalled. Restart your MCP client.\n`);
  });

// ── completion ─────────────────────────────────────────────────────────────

function generateCompletionScript(shell: string): string | null {
  const cmdNames = program.commands.map((c) => c.name()).join(" ");
  const optNames = ["--help", "--json", "--no-color", "--version"].join(" ");
  const all = `${optNames} ${cmdNames}`;

  if (shell === "bash") {
    return [
      "_openplan_completions() {",
      "  local cur prev opts; COMPREPLY=()",
      '  cur="${COMP_WORDS[COMP_CWORD]}"',
      '  prev="${COMP_WORDS[COMP_CWORD-1]}"',
      `  opts="${all}"`,
      '  if [[ ${cur} == -* ]]; then COMPREPLY=( $(compgen -W "${opts}" -- ${cur}) )',
      '  else COMPREPLY=( $(compgen -W "${opts}" -- ${cur}) )',
      "  fi",
      "  return 0",
      "}",
      "complete -F _openplan_completions openplan",
    ].join("\n");
  }

  if (shell === "zsh") {
    const cmds = cmdNames.split(" ").join(" ");
    return [
      "#compdef openplan",
      "_arguments \\",
      "  '--help[display help]' \\",
      "  '--json[output in JSON format]' \\",
      "  '--no-color[disable color output]' \\",
      "  '--version[show version]' \\",
      `  "1: :(${cmds})" \\`,
      "  '*::arg:->args'",
    ].join("\n");
  }

  if (shell === "fish") {
    return [
      "function _openplan_completions",
      `  set -l cmds ${cmdNames}`,
      '  complete -c openplan -f -a "$cmds" -d "OpenPlan command"',
      '  complete -c openplan -l help -d "display help"',
      '  complete -c openplan -l json -d "output in JSON format"',
      '  complete -c openplan -l no-color -d "disable color output"',
      '  complete -c openplan -l version -d "show version"',
      "end",
      "_openplan_completions",
    ].join("\n");
  }

  return null;
}

program
  .command("completion")
  .description("Generate shell completion script")
  .argument("[shell]", "Shell type: bash, zsh, fish", "bash")
  .action((shell: string) => {
    const script = generateCompletionScript(shell);
    if (!script) {
      console.error(`  ${pc.red("!")} Unsupported shell: "${shell}". Supported: bash, zsh, fish.\n`);
      process.exit(1);
    }
    console.log(script.trimStart());
  });

// ── subscribe ─────────────────────────────────────────────────────────────────

program
  .command("subscribe")
  .description("Subscribe to Pro (Stripe Checkout)")
  .action(async () => {
    const config = loadConfig();
    if (!config.apiKey) {
      console.error(`  ${pc.yellow("!")} Not authenticated. Run \`openplan auth\` first.\n`);
      process.exit(1);
    }

    const base = meshUrl();
    try {
      const resp = await fetch(`${base}/v1/subscribe`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan: "pro", api_key: config.apiKey }),
      });
      if (!resp.ok) {
        const errRaw = await resp.json().catch(() => null);
        const err = errRaw ? ErrorDetailResponse.parse(errRaw) : null;
        console.error(
          `  ${pc.red("!")} ${err?.detail ? `Subscribe failed: ${err.detail}` : `Subscribe failed (${resp.status})`}\n`,
        );
        process.exit(1);
      }
      const dataRaw = await resp.json();
      const data = SubscribeResponse.parse(dataRaw);
      const url = data.checkout_url;

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
          log.debug("Browser open failed");
        }
      }
    } catch (e) {
      console.error(`  ${pc.red("!")} Subscribe failed: ${e instanceof Error ? e.message : "unknown error"}\n`);
      process.exit(1);
    }
  });

// ── portal ────────────────────────────────────────────────────────────────────

program
  .command("portal")
  .description("Manage subscription (Stripe Customer Portal)")
  .action(async () => {
    const config = loadConfig();
    if (!config.apiKey) {
      console.error(`  ${pc.yellow("!")} Not authenticated. Run \`openplan auth\` first.\n`);
      process.exit(1);
    }

    const base = meshUrl();
    try {
      const resp = await fetch(`${base}/v1/manage`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${config.apiKey}`,
        },
      });
      if (!resp.ok) {
        const errRaw = await resp.json().catch(() => null);
        const err = errRaw ? ErrorDetailResponse.parse(errRaw) : null;
        console.error(`  ${pc.red("!")} ${err?.detail ?? `Portal failed (${resp.status})`}\n`);
        process.exit(1);
      }
      const dataRaw = await resp.json();
      const data = PortalResponse.parse(dataRaw);
      const url = data.url;
      console.error(`  ${pc.dim(">")}  Opening billing portal...\n`);
      try {
        const { execSync } = await import("node:child_process");
        execSync(`open "${url}"`, { timeout: 3000 });
      } catch {
        log.debug("Browser open failed");
      }
    } catch (e) {
      console.error(`  ${pc.red("!")} Portal failed: ${e instanceof Error ? e.message : "unknown error"}\n`);
      process.exit(1);
    }
  });

// ── account ──────────────────────────────────────────────────────────────────

program
  .command("account")
  .description("Account info, subscription status, or delete")
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

      console.error(`  ${pc.red("!")} This will permanently delete all your calibration data from the Mesh`);
      console.error("    and revoke your API key. This cannot be undone.");
      console.error("");

      if (process.stdout.isTTY && !process.env.CI) {
        const { createInterface } = await import("node:readline");
        const rl = createInterface({ input: process.stdin, output: process.stderr });
        const answer = await new Promise<string>((resolve) => {
          rl.question(`  ${pc.dim(">")} Are you sure? [y/N] `, resolve);
        });
        rl.close();
        if (answer.trim().toLowerCase() !== "y") {
          console.error(`  ${pc.yellow(">")} Account deletion cancelled.\n`);
          return;
        }
      }

      try {
        const resp = await fetch(`${base}/v1/account/delete`, {
          method: "POST",
          headers: { Authorization: `Bearer ${config.apiKey}` },
        });
        if (!resp.ok) {
          const errRaw = await resp.json().catch(() => null);
          const err = errRaw ? ErrorDetailResponse.parse(errRaw) : null;
          console.error(`  ${pc.red("!")} Delete failed: ${err?.detail ?? `HTTP ${resp.status}`}\n`);
          process.exit(1);
        }
        console.error(`  ${pc.green("*")} Account data deleted from Mesh.\n`);
      } catch (e) {
        console.error(`  ${pc.red("!")} Delete failed: ${e instanceof Error ? e.message : "unknown error"}\n`);
      }
      return;
    }

    let meshReachable = false;
    let subStatus: { tier: string; status: string } | null = null;
    if (config.apiKey) {
      try {
        const resp = await fetch(`${base}/v1/account`, {
          headers: { Authorization: `Bearer ${config.apiKey}` },
          signal: AbortSignal.timeout(3000),
        });
        if (resp.ok) {
          const body = await resp.json();
          const parsed = AccountResponse.parse(body);
          subStatus = { tier: parsed.tier, status: parsed.status ?? "active" };
          meshReachable = true;
        }
      } catch {
        log.debug("Mesh unreachable");
      }
    }

    if (program.opts().json) {
      console.log(
        JSON.stringify(
          {
            identityId: config.identityId,
            dataDir: config.dataDir,
            apiKey: config.apiKey ? "configured" : null,
            meshReachable,
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
      if (!meshReachable && config.apiKey) {
        console.error(`  ${pc.dim("-")}  Mesh: ${pc.red("unreachable")} — subscription status may be stale`);
      }
      if (subStatus) {
        console.error(`  ${pc.dim("-")}  Subscription: ${subStatus.tier} — ${subStatus.status}`);
      } else {
        console.error(`  ${pc.dim("-")}  Subscription: ${pc.dim("free (unauthenticated)")}`);
      }
      console.error("");
    }
  });

// ── config ───────────────────────────────────────────────────────────────────

program
  .command("config")
  .description("Display configuration")
  .argument("[action]", "Action: show")
  .action((action?: string) => {
    if (action && action !== "show") {
      console.error(`  ${pc.red("!")} Unknown action "${action}". Use \`openplan config show\`.\n`);
      process.exit(1);
    }
    const config = loadConfig();
    if (program.opts().json) {
      console.log(JSON.stringify(config, null, 2));
    } else {
      console.error(`  ${pc.dim("-")}  Config file: ${getConfigPath()}`);
      console.error(`  ${pc.dim("-")}  Data directory: ${getDataDir()}`);
      console.error(`  ${pc.dim("-")}  Identity: ${config.identityId}`);
      console.error(`  ${pc.dim("-")}  Mesh: ${config.meshUrl ? pc.green("enabled") : pc.dim("disabled")}`);
      console.error(`  ${pc.dim("-")}  Mesh URL: ${config.meshUrl ?? pc.dim("none")}`);
      console.error(`  ${pc.dim("-")}  API Key: ${config.apiKey ? pc.green("configured") : pc.dim("not configured")}`);
      console.error(`  ${pc.dim("-")}  Cost Probe: ${config.costProbeCommand ?? pc.dim("not configured")}`);
      console.error("");
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
      saveConfig({ meshUrl: DEFAULT_MESH_URL });
      console.error(`  ${pc.green("*")} Mesh sync enabled.\n`);
      return;
    }

    if (action === "off") {
      const tomlPath = getConfigPath();
      try {
        const raw = readFileSync(tomlPath, "utf-8");
        const parsed: unknown = parse(raw);
        const doc = parsed as Record<string, unknown>;
        const meshRaw = doc.mesh;
        const mesh: Record<string, unknown> =
          meshRaw && typeof meshRaw === "object" ? { ...(meshRaw as Record<string, unknown>) } : {};
        doc.mesh = { ...mesh, enabled: false, url: DEFAULT_MESH_URL };
        writeFileSync(tomlPath, stringify(doc), "utf-8");
        console.error(`  ${pc.green("*")} Mesh sync disabled.\n`);
      } catch (e) {
        console.error(`  ${pc.red("!")} Failed: ${e instanceof Error ? e.message : "unknown error"}\n`);
        process.exit(1);
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

    if (program.opts().json) {
      console.log(JSON.stringify({ enabled: meshEnabled, url: config.meshUrl, pending, synced }, null, 2));
    } else {
      console.error(`  ${pc.dim("-")}  Mesh: ${meshEnabled ? pc.green("enabled") : pc.red("disabled")}`);
      console.error(`  ${pc.dim("-")}  URL: ${config.meshUrl ?? pc.dim("none")}`);
      console.error(`  ${pc.dim("-")}  Pending: ${pending}`);
      console.error(`  ${pc.dim("-")}  Synced: ${synced}`);
      console.error("");
    }
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
  .description("Show checkpoint trail (optionally filtered by route ID)")
  .argument("[route-id]", "Route ID to filter by")
  .action((routeId?: string) => {
    const config = loadConfig();
    const dbPath = join(getDataDir(), "openplan.db");
    if (!existsSync(dbPath)) {
      console.error(`  ${pc.dim("-")}  No data found.`);
      return;
    }
    const db = openDatabase(dbPath);
    const store = createStore(db, config.identityId);

    const events = routeId ? store.getCalibrationEventsForRoute(routeId) : store.getCalibrationEvents();

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
      // Local-only export (JSON only)
      const dbPath = join(getDataDir(), "openplan.db");
      if (!existsSync(dbPath)) {
        console.error(`  ${pc.dim("-")}  No data found.`);
        return;
      }
      if (fmt !== "json") {
        console.error(
          `  ${pc.yellow("!")} Local export supports --format json only. Run \`openplan auth\` to connect to Mesh for CSV/Markdown.\n`,
        );
        return;
      }
      const db = openDatabase(dbPath);
      const store = createStore(db, config.identityId);

      const allRoutes = options.project
        ? store.getRoutesForProject(options.project)
        : (store
            .getCalibrationEvents()
            .reduce((acc: string[], e) => {
              if (e.routeId && !acc.includes(e.routeId)) acc.push(e.routeId);
              return acc;
            }, [])
            .map((id) => store.getRoute(id))
            .filter(Boolean) as NonNullable<ReturnType<typeof store.getRoute>>[]);

      if (allRoutes.length === 0) {
        console.error(`  ${pc.yellow("!")} No routes found for export.\n`);
        return;
      }

      console.log(
        JSON.stringify(
          {
            exported_at: new Date().toISOString(),
            routes: allRoutes.map((r) => ({
              project: r.project,
              goal: r.goal,
              status: r.status,
              phases: store.getPhases(r.id),
            })),
            calibrations: store.getCalibrationEvents(),
          },
          null,
          2,
        ),
      );
      return;
    }

    // Mesh-backed export (cross-machine)
    try {
      const resp = await fetch(`${base}/v1/export`, {
        headers: { Authorization: `Bearer ${config.apiKey}` },
      });
      if (!resp.ok) {
        const errRaw = await resp.json().catch(() => null);
        const err = errRaw ? ErrorDetailResponse.parse(errRaw) : null;
        const detail = err?.detail ?? `HTTP ${resp.status}`;
        console.error(`  ${pc.red("!")} Export failed: ${detail}\n`);
        process.exit(1);
      }
      const dataRaw = await resp.json();
      const data = ExportResponse.parse(dataRaw);

      if (fmt === "csv") {
        const calibrations = data.calibrations ?? [];
        console.log("action,expected_cost,actual_cost,outcome,session_id,created_at");
        for (const c of calibrations) {
          console.log(
            `${c.action ?? ""},${c.expected_cost ?? ""},${c.actual_cost ?? ""},${c.outcome ?? ""},${c.session_id ?? ""},${c.created_at ?? ""}`,
          );
        }
      } else if (fmt === "markdown") {
        const summary = data.summary;
        const calibrations = data.calibrations ?? [];
        console.log("# OpenPlan Data Export\n");
        console.log(`Exported at: ${new Date(data.exported_at * 1000).toISOString()}`);
        console.log(`Tier: ${data.tier ?? "unknown"}`);
        console.log("\n## Summary\n");
        console.log(`- Total calibrations: ${summary?.total_calibrations ?? 0}`);
        console.log("\n### Accuracy by Action\n");
        console.log("| Action | Samples | Mean Deviation | MAPE |");
        console.log("|--------|---------|---------------|------|");
        const accuracy = summary?.accuracy_by_action ?? [];
        for (const a of accuracy) {
          console.log(
            `| ${a.action ?? ""} | ${a.sample_count ?? 0} | ${a.mean_deviation ?? "-"} | ${a.mape ?? "-"}% |`,
          );
        }
        console.log("\n## Calibration Events\n");
        for (const c of calibrations) {
          console.log(
            `- ${c.created_at ?? ""} | ${c.action ?? ""} | actual: ${c.actual_cost ?? ""} | expected: ${c.expected_cost ?? "?"} | ${c.outcome ?? ""}`,
          );
        }
      } else {
        console.log(JSON.stringify(data, null, 2));
      }
    } catch (e) {
      console.error(`  ${pc.red("!")} Export failed: ${e instanceof Error ? e.message : "unknown error"}\n`);
      process.exit(1);
    }
  });

// ── doctor ────────────────────────────────────────────────────────────────────

program
  .command("doctor")
  .description("Check system health and diagnose issues")
  .action(async () => {
    const config = loadConfig();
    const base = meshUrl();
    let ok = true;

    async function check(label: string, fn: () => Promise<string | null>): Promise<void> {
      try {
        const msg = await fn();
        if (msg === null) {
          console.error(`  ${pc.green("*")} ${label}`);
        } else {
          console.error(`  ${pc.red("!")} ${label}: ${msg}`);
          ok = false;
        }
      } catch (e) {
        console.error(`  ${pc.red("!")} ${label}: ${e instanceof Error ? e.message : "unknown error"}`);
        ok = false;
      }
    }

    console.error("");

    await check("Node.js version", async () => {
      const [major] = process.versions.node.split(".").map(Number);
      return major >= 20 ? null : `Node.js ${process.versions.node} (<20) — upgrade required`;
    });

    await check("Config file", async () => {
      const cfgPath = getConfigPath();
      if (!existsSync(cfgPath)) return "not found — will be auto-created on first MCP server start";
      try {
        const raw = readFileSync(cfgPath, "utf-8");
        parse(raw);
        return null;
      } catch {
        return "invalid TOML — check the file format";
      }
    });

    await check("SQLite database", async () => {
      const dbPath = join(getDataDir(), "openplan.db");
      if (!existsSync(dbPath)) return "not found — run a plan() to create it";
      const db = openDatabase(dbPath);
      const row = db.$client.prepare("SELECT COUNT(*) AS cnt FROM calibration_events").get() as
        | { cnt: number | null }
        | undefined;
      if (row && typeof row.cnt === "number") {
        console.error(`  ${pc.green("*")} OK — ${row.cnt} calibration events`);
      }
      return "skip";
    });

    await check("Identity", async () => {
      if (!config.identityId) return "not generated";
      if (!/^[0-9a-f-]{36}$/i.test(config.identityId)) return "invalid UUID format";
      return null;
    });

    await check("Mesh connectivity", async () => {
      const resp = await fetch(`${base}/v1/health`, { signal: AbortSignal.timeout(5000) });
      if (!resp.ok) return `unreachable (HTTP ${resp.status})`;
      return null;
    });

    await check("API key", async () => {
      if (!config.apiKey) return "not configured — run `openplan auth`";
      const resp = await fetch(`${base}/v1/account`, {
        headers: { Authorization: `Bearer ${config.apiKey}` },
        signal: AbortSignal.timeout(5000),
      });
      if (resp.status === 404) return "invalid or revoked — run `openplan auth` again";
      if (!resp.ok) return `unreachable (HTTP ${resp.status})`;
      return null;
    });

    await check("Subscription", async () => {
      if (!config.apiKey) return "not authenticated";
      const resp = await fetch(`${base}/v1/account`, {
        headers: { Authorization: `Bearer ${config.apiKey}` },
        signal: AbortSignal.timeout(5000),
      });
      if (!resp.ok) return "unreachable";
      const body = await resp.json();
      const parsed = AccountResponse.parse(body);
      if (parsed.tier === "pro") {
        console.error(`  ${pc.green("*")} Pro`);
      } else {
        console.error(`  ${pc.green("*")} Free`);
      }
      return "skip";
    });

    await check("Disk space", async () => {
      const { execSync } = await import("node:child_process");
      const out = execSync("df -k /tmp").toString().trim().split("\n").pop()?.split(/\s+/);
      if (out?.[3]) {
        const freeKB = Number.parseInt(out[3], 10);
        if (freeKB < 100_000) return `low disk space (~${Math.round(freeKB / 1024)}MB free)`;
      }
      return null;
    });

    console.error("");
    if (!ok) {
      console.error(`  ${pc.yellow("?")} Some checks failed. Run \`openplan doctor\` again after fixing.\n`);
      process.exit(1);
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
} else if (firstNonFlag) {
  console.error(`${pc.red("!")} Unknown command "${firstNonFlag}". Run \`openplan --help\` for usage.\n`);
  process.exit(1);
} else {
  startServer().catch((e) => {
    console.error(
      `${pc.red("!")} Failed to start OpenPlan MCP server: ${e instanceof Error ? e.message : "unknown error"}`,
    );
    process.exit(1);
  });
}
