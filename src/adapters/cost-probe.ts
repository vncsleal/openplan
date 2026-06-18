import { execSync } from "child_process";
import type { CostProbe } from "../core/ports.js";

export class ShellCostProbe implements CostProbe {
  private command: string;
  private startTokens: number | null = null;

  constructor(command: string) {
    this.command = command;
  }

  async start(): Promise<void> {
    try {
      const output = execSync(this.command, { encoding: "utf-8", timeout: 5000 });
      this.startTokens = this.parseTokenCount(output);
    } catch {
      this.startTokens = null;
    }
  }

  async stop(): Promise<number | null> {
    if (this.startTokens === null) return null;

    try {
      const output = execSync(this.command, { encoding: "utf-8", timeout: 5000 });
      const endTokens = this.parseTokenCount(output);
      if (endTokens === null || endTokens <= this.startTokens) return null;
      return endTokens - this.startTokens;
    } catch {
      return null;
    } finally {
      this.startTokens = null;
    }
  }

  private parseTokenCount(output: string): number | null {
    try {
      const data = JSON.parse(output);
      // Support both opencode stats --json and other formats
      if (typeof data.totalTokens === "number") return data.totalTokens;
      if (typeof data.tokens === "number") return data.tokens;
      if (typeof data.tokenCount === "number") return data.tokenCount;
      if (typeof data.cost === "number") return Math.round(data.cost);
      return null;
    } catch {
      // Try to find a number in the output
      const match = output.trim().match(/^(\d+)$/);
      return match ? Number.parseInt(match[1], 10) : null;
    }
  }
}
