import { describe, expect, it } from "vitest";
import {
  approvalBlocked,
  editDraft,
  edited,
  freshDraft,
  openDraft,
  refetched,
  refetchDrafts,
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
    p = saved(refetched(p, v2), 3, "typing");
    expect(p).toEqual({ draft: "typing", baseVersion: 3, dirty: false, serverNewer: 0 });
  });

  it("typing while a save is in flight stays dirty after that save lands", () => {
    // P2 regression: the save response used to clobber edits typed mid-save.
    let p = edited(selectArtifact(v1), "sent");
    const savingFrom = p.draft;
    p = edited(p, "sent and then more typing"); // landed while the PUT was in flight
    p = saved(p, 2, savingFrom); // the response reports the content that was saved
    expect(p.draft).toBe("sent and then more typing");
    expect(p.dirty).toBe(true); // and approval stays blocked
    expect(p.baseVersion).toBe(2);
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
    const clean = { "a.md": selectArtifact(v1) };
    expect(approvalBlocked(clean, false)).toBeNull();
    expect(approvalBlocked({ "a.md": edited(clean["a.md"], "typing") }, false)).toContain("a.md");
    expect(approvalBlocked(clean, true)).toBe("a save is still in flight");
    // a refetch that found a newer server version while dirty flags it; the
    // draft is dirty too, so approval stays blocked until it is resolved
    const conflicted = refetched(edited(clean["a.md"], "typing"), v2);
    expect(conflicted.serverNewer).toBe(2);
    expect(approvalBlocked({ "a.md": conflicted }, false)).toBeTruthy();
    // with the flag set even on a resolved draft, approval is still blocked
    expect(approvalBlocked({ "a.md": { ...conflicted, dirty: false } }, false)).toContain("server");
  });

  it("saving clears the block (fresh draft = what the operator just saw)", () => {
    let drafts = { "a.md": edited(selectArtifact(v1), "my edit") };
    expect(approvalBlocked(drafts, false)).toBeTruthy();
    drafts = { "a.md": saved(drafts["a.md"], 2, drafts["a.md"].draft) };
    expect(approvalBlocked(drafts, false)).toBeNull();
  });

  it("a dirty draft on ANY artifact blocks approval, even a non-selected one", () => {
    // P2 regression: one shared draft meant selecting another artifact wiped
    // the dirty flag and silently re-enabled Approve.
    const drafts = {
      "a.md": selectArtifact(v1),
      "b.md": edited(selectArtifact(v1), "half-finished edit"),
    };
    expect(approvalBlocked(drafts, false)).toContain("b.md");
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

describe("per-artifact drafts", () => {
  it("opening an artifact adopts the server copy once; re-selecting never wipes edits", () => {
    let drafts = openDraft({}, "a.md", v1);
    drafts = editDraft(drafts, "a.md", "typing");
    drafts = openDraft(drafts, "a.md", v2); // selecting it again while on v2
    expect(drafts["a.md"].draft).toBe("typing"); // P2: switching never discards
  });

  it("edits to one artifact leave the other's draft untouched", () => {
    let drafts = openDraft(openDraft({}, "a.md", v1), "b.md", v1);
    drafts = editDraft(drafts, "a.md", "only a changes");
    expect(drafts["b.md"].draft).toBe("server v1");
    expect(drafts["b.md"].dirty).toBe(false);
  });

  it("a refetch preserves every dirty draft and flags every divergence", () => {
    let drafts = {
      "a.md": edited(selectArtifact(v1), "typing a"),
      "b.md": selectArtifact(v1),
    };
    drafts = refetchDrafts(drafts, { "a.md": v2, "b.md": v2 });
    expect(drafts["a.md"]).toEqual({ draft: "typing a", baseVersion: 1, dirty: true, serverNewer: 2 });
    expect(drafts["b.md"]).toEqual({ draft: "server v2", baseVersion: 2, dirty: false, serverNewer: 0 });
  });
});
