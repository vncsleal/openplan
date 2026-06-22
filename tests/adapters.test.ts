import { describe, it, expect } from "vitest";
import { createNullCostProbe, createShellCostProbe } from "../src/adapters/cost-probe.js";

describe("NullCostProbe", () => {
  const probe = createNullCostProbe();

  it("start does not throw", () => {
    expect(() => probe.start()).not.toThrow();
  });

  it("stop returns null", () => {
    expect(probe.stop()).toBeNull();
  });

  it("is idempotent across multiple calls", () => {
    probe.start();
    expect(probe.stop()).toBeNull();
    probe.start();
    expect(probe.stop()).toBeNull();
  });
});

describe("ShellCostProbe", () => {
  it("returns null for invalid command", () => {
    const probe = createShellCostProbe("nonexistent-command-12345");
    probe.start();
    const result = probe.stop();
    expect(result).toBeNull();
  });

  it("returns number for echo command", () => {
    const probe = createShellCostProbe("echo 42");
    probe.start();
    const result = probe.stop();
    expect(result).toBe(42);
  });

  it("returns null for non-numeric output", () => {
    const probe = createShellCostProbe("echo hello");
    probe.start();
    const result = probe.stop();
    expect(result).toBeNull();
  });

  it("handles empty output gracefully", () => {
    const probe = createShellCostProbe("echo");
    probe.start();
    const result = probe.stop();
    expect(result).toBeNull();
  });

  it("handles whitespace output", () => {
    const probe = createShellCostProbe("echo '  '");
    probe.start();
    const result = probe.stop();
    expect(result).toBeNull();
  });
});
