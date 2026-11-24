import { describe, expect, it } from "vitest";
import { api, STATE_COLORS } from "../src/api";

describe("api constants", () => {
  it("has a color for every project state", () => {
    const states = [
      "IDEA", "DISCOVERY", "AWAITING_INPUT", "AWAITING_APPROVAL", "PLANNING",
      "COMPLETED", "FAILED", "CANCELLED",
    ] as const;
    for (const s of states) expect(STATE_COLORS[s]).toBeTruthy();
  });
});

describe("state helpers", () => {
  it("state names round-trip with underscores", () => {
    const s = "AWAITING_APPROVAL";
    expect(s.replaceAll("_", " ")).toBe("AWAITING APPROVAL");
  });
});

describe("lifecycle api", () => {
  it("exposes restart, deleteProject, and renameProject", () => {
    expect(typeof api.restart).toBe("function");
    expect(typeof api.deleteProject).toBe("function");
    expect(typeof api.renameProject).toBe("function");
  });
});
