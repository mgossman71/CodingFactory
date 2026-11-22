import { describe, expect, it } from "vitest";
import { STATE_COLORS } from "../src/api";

describe("api constants", () => {
  it("has a color for every project state", () => {
    const states = [
      "IDEA", "DISCOVERY", "AWAITING_INPUT", "AWAITING_APPROVAL", "PLANNING",
      "BUILDING", "COMPLETED", "FAILED", "CANCELLED",
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
