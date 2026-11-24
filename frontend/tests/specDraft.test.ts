import { describe, expect, it } from "vitest";
import {
  approvalBlocked,
  edited,
  freshDraft,
  refetched,
  restoredFromHistory,
  reviewedVersionMap,
  saved,
  selectArtifact,
} from "../src/specDraft";

const v1 = { current_version: 1, content: "server v1" };
const v2 = { current_version: 2, content: "server v2" };

describe("spec draft policy", () => {
  it("selecting an artifact adopts the server copy", () => {
    expect(selectArtifact(v1)).toEqual({
      draft: "server v1",
      baseVersion: 1,
      dirty: false,
      serverNewer: 0,
    });
  });

  it("a clean draft adopts a newer server version on refresh", () => {
    const p = refetched(selectArtifact(v1), v2);
    expect(p.draft).toBe("server v2");
    expect(p.baseVersion).toBe(2);
    expect(p.serverNewer).toBe(0);
  });

  it("preserves unsaved work across refreshes and flags the newer server version", () => {
    let p = edited(selectArtifact(v1), "typing something");
    p = refetched(p, v2);
    expect(p.draft).toBe("typing something");
    expect(p.dirty).toBe(true);
    expect(p.baseVersion).toBe(1);
    expect(p.serverNewer).toBe(2);
  });

  it("unsaved work survives repeated 5s polls", () => {
    let p = edited(selectArtifact(v1), "typing");
    p = refetched(refetched(refetched(p, v2), v2), v2);
    expect(p.draft).toBe("typing");
    expect(p.serverNewer).toBe(2);
  });

  it("no-op when the server version has not moved", () => {
    const p = edited(selectArtifact(v1), "typing");
    expect(refetched(p, v1)).toBe(p);
  });

  it("a vanished artifact leaves the draft alone", () => {
    const p = edited(freshDraft, "typing");
    expect(refetched(p, undefined)).toBe(p);
  });

  it("saving clears the dirty flag and the divergence flag", () => {
    let p = edited(selectArtifact(v1), "typing");
    p = saved(refetched(p, v2), 3);
    expect(p).toEqual({ draft: "typing", baseVersion: 3, dirty: false, serverNewer: 0 });
  });

  it("loading the flagged server version discards the local draft", () => {
    let p = edited(selectArtifact(v1), "typing");
    p = refetched(p, v2);
    p = selectArtifact(v2); // the explicit "discard my edits, load v2" action
    expect(p).toEqual({ draft: "server v2", baseVersion: 2, dirty: false, serverNewer: 0 });
  });

  it("restoring from history counts as an edit (protected from refresh)", () => {
    let p = restoredFromHistory(selectArtifact(v1), "old version text");
    p = refetched(p, v2);
    expect(p.draft).toBe("old version text");
    expect(p.dirty).toBe(true);
  });
});

describe("approval gate (D-029)", () => {
  it("approve is blocked by a dirty draft, an in-flight save, or an unresolved conflict", () => {
    const clean = selectArtifact(v1);
    expect(approvalBlocked(clean, false)).toBeNull();
    expect(approvalBlocked(edited(clean, "typing"), false)).toBe("you have unsaved edits");
    expect(approvalBlocked(clean, true)).toBe("a save is still in flight");
    // a refetch that found a newer server version while dirty flags it; the
    // draft is dirty too, so approval stays blocked until it is resolved
    const conflicted = refetched(edited(clean, "typing"), v2);
    expect(conflicted.serverNewer).toBe(2);
    expect(approvalBlocked(conflicted, false)).toBeTruthy();
    // with the flag set even on a resolved draft, approval is still blocked
    expect(approvalBlocked({ ...conflicted, dirty: false }, false)).toContain("server");
  });

  it("saving clears the block (fresh draft = what the operator just saw)", () => {
    let p = edited(selectArtifact(v1), "my edit");
    expect(approvalBlocked(p, false)).toBeTruthy();
    p = saved(p, 2);
    expect(approvalBlocked(p, false)).toBeNull();
  });

  it("the reviewed version map mirrors the eligible spec: no handoff, no empty artifacts", () => {
    const map = reviewedVersionMap([
      { slug: "product-requirements.md", current_version: 2 },
      { slug: "open-questions.md", current_version: 1 },
      { slug: "handoff-prompt.md", current_version: 3 },
      { slug: "design-system.md", current_version: 0 },
    ]);
    expect(map).toEqual({ "product-requirements.md": 2, "open-questions.md": 1 });
  });
});
