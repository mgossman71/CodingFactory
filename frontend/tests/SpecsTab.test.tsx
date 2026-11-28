// @vitest-environment jsdom
/**
 * SpecsTab component tests (code-review round 3 asked for delayed-response
 * coverage). Closes the long-standing "no jsdom" gap for the approval flow:
 *
 *  1. a save in flight disables Save (requests serialize) AND blocks Approve,
 *     and only the DELAYED response releases the block — a completed save can
 *     never re-enable Approve while saving is unresolved;
 *  2. drafts survive a tab switch (unmount + remount), because ProjectPage
 *     owns them — returning to Specs must not silently enable approval.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { act } from "react";
import SpecsTab from "../src/components/SpecsTab";
import { api, Artifact, ProjectState, Review } from "../src/api";
import { useDraftGeneration } from "../src/useDraftGeneration";

// Direct react act() use (deferred promise resolution) needs the flag;
// @testing-library/react normally sets it around its own renders.
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../src/api", () => ({
  api: {
    artifacts: vi.fn(),
    artifactHistory: vi.fn(),
    review: vi.fn(),
    saveArtifact: vi.fn(),
    approval: vi.fn(),
  },
  AGENT_STYLES: {},
  STATE_COLORS: {},
}));

const ARTIFACT: Artifact = {
  slug: "product-requirements.md",
  title: "Product Requirements",
  current_version: 1,
  content: "server text",
  updated_at: "2026-01-01T00:00:00Z",
};

const REVIEW: Review = {
  in_scope: ["build the thing"],
  out_of_scope: [],
  flagged_assumptions: null, // no flagged section in the sliced docs
  assumptions: [],
  decisions: [],
  decision_history: null,
  interpretations: [],
  unresolved: [],
  acceptance_criteria: [],
  sources: [{ slug: "product-requirements.md", title: "Product Requirements", version: 1, version_id: "vid-1" }],
};

function useProjectPageState(projectId = "p1") {
  // The SAME shared hook ProjectPage uses: drafts, pending saves AND their
  // generation all come from it, so nothing here re-declares page logic.
  // Page-level behaviour (route switches, reload races) is covered against
  // the real ProjectPage in ProjectPage.test.tsx.
  const gen = useDraftGeneration(projectId);
  const props = {
    generation: gen.generation,
    drafts: gen.drafts,
    pendingSaves: gen.pendingSaves,
    updateDrafts: gen.updateDrafts,
    updatePendingSaves: gen.updatePendingSaves,
  };
  return { props, gen };
}

function Wrapper({ state = "AWAITING_APPROVAL" }: { state?: ProjectState }) {
  const page = useProjectPageState();
  return (
    <SpecsTab
      state={state}
      version={0}
      onAction={() => {}}
      {...page.props}
    />
  );
}

const button = (name: RegExp) => screen.getByRole("button", { name }) as HTMLButtonElement;

// The approval gate lands on the review panel; the editor opens by clicking
// the artifact in the sidebar (same as a real operator would).
async function openEditor() {
  const textarea = await screen.findByDisplayValue("server text").catch(() => null);
  if (textarea) return textarea;
  fireEvent.click(button(/Product Requirements/));
  return screen.findByDisplayValue("server text");
}

beforeEach(() => {
  vi.clearAllMocks(); // call counts must not leak between tests
  vi.mocked(api.artifacts).mockResolvedValue([ARTIFACT]);
  vi.mocked(api.artifactHistory).mockResolvedValue([]);
  vi.mocked(api.review).mockResolvedValue(REVIEW);
  vi.mocked(api.saveArtifact).mockResolvedValue({ ...ARTIFACT, current_version: 2 });
  vi.mocked(api.approval).mockResolvedValue({} as never);
});

afterEach(() => cleanup());

describe("SpecsTab save/approval interlock", () => {
  it("a save in flight serializes Save and blocks Approve until the delayed response lands", async () => {
    let resolveSave!: (a: Artifact) => void;
    vi.mocked(api.saveArtifact).mockImplementation(
      () => new Promise<Artifact>((r) => { resolveSave = r; }),
    );

    render(<Wrapper />);
    const textarea = await openEditor();

    fireEvent.change(textarea, { target: { value: "edited text" } });
    fireEvent.click(button(/Save as new version/));

    await waitFor(() => expect(api.saveArtifact).toHaveBeenCalledTimes(1));
    // In flight: Save is disabled (no second save can overlap), and Approve
    // is blocked by the pending save — not by dirty state (the saved content
    // matches), proving the interlock comes from the pending counter.
    expect(button(/Saving|Save as new version/).disabled).toBe(true);
    const approve = button(/Approve & generate build prompt/);
    expect(approve.disabled).toBe(true);
    expect(screen.getByText(/a save is still in flight/)).toBeTruthy();

    // A second click cannot fire a second save.
    fireEvent.click(button(/Saving/));
    expect(api.saveArtifact).toHaveBeenCalledTimes(1);

    // Only the delayed RESPONSE releases the block.
    await act(async () => { resolveSave({ ...ARTIFACT, current_version: 2 }); });
    await waitFor(() => expect(button(/Approve & generate build prompt/).disabled).toBe(false));
  });

  it("typing while a save is in flight keeps Approve blocked after the response", async () => {
    let resolveSave!: (a: Artifact) => void;
    vi.mocked(api.saveArtifact).mockImplementation(
      () => new Promise<Artifact>((r) => { resolveSave = r; }),
    );

    render(<Wrapper />);
    const textarea = await openEditor();
    fireEvent.change(textarea, { target: { value: "first" } });
    fireEvent.click(button(/Save as new version/));
    await waitFor(() => expect(api.saveArtifact).toHaveBeenCalledTimes(1));

    fireEvent.change(textarea, { target: { value: "first + more typed mid-save" } });
    await act(async () => { resolveSave({ ...ARTIFACT, current_version: 2, content: "first" }); });

    // The save accepted "first"; the draft has moved on -> still dirty/blocked.
    await waitFor(() => expect(button(/Approve & generate build prompt/).disabled).toBe(true));
    expect(screen.getByText(/unsaved edits in product-requirements.md/)).toBeTruthy();
  });
});

describe("SpecsTab flagged assumptions (D-047)", () => {
  it("high-impact assumptions get a confirm block on the review", async () => {
    vi.mocked(api.review).mockResolvedValue({
      ...REVIEW,
      flagged_assumptions: ["single user, no auth — changes what gets built"],
    });
    render(<Wrapper />);
    const item = await screen.findByText(/single user, no auth — changes what gets built/);
    const confirm = screen.getByText(/Confirm these assumptions — high impact if wrong/);
    expect(item.compareDocumentPosition(confirm) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
    // The block is ordered FIRST — before In scope — it is the one to read.
    const inScope = screen.getByText("In scope");
    expect(confirm.compareDocumentPosition(inScope) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("no flagged section means no block", async () => {
    render(<Wrapper />); // REVIEW.flagged_assumptions === null
    await screen.findByRole("heading", { name: /Approval review/ });
    expect(screen.queryByText(/Confirm these assumptions/)).toBeNull();
  });
});

describe("SpecsTab drafts across tab switches", () => {
  function TabSwitch() {
    // Mirrors ProjectPage: drafts and pending saves outlive SpecsTab.
    const page = useProjectPageState();
    const [show, setShow] = useState(true);
    return (
      <>
        <button onClick={() => setShow((s) => !s)}>toggle-specs</button>
        {show && (
          <SpecsTab
            state="AWAITING_APPROVAL"
            version={0}
            onAction={() => {}}
            {...page.props}
          />
        )}
      </>
    );
  }

  it("unmount + remount (Discussion/Activity tab switch) preserves drafts and the approval block", async () => {
    render(<TabSwitch />);
    const textarea = await openEditor();
    fireEvent.change(textarea, { target: { value: "half-finished spec edit" } });
    expect(button(/Approve & generate build prompt/).disabled).toBe(true);

    fireEvent.click(button(/toggle-specs/)); // leave the Specs tab
    await waitFor(() => expect(screen.queryByDisplayValue("half-finished spec edit")).toBeNull());

    fireEvent.click(button(/toggle-specs/)); // come back (lands on the review panel again)
    await screen.findByRole("button", { name: /Product Requirements/ });
    fireEvent.click(button(/Product Requirements/));
    const restored = await screen.findByDisplayValue("half-finished spec edit");
    expect(restored).toBeTruthy();
    // Approval stays blocked: the edits are still there, not silently lost.
    expect(button(/Approve & generate build prompt/).disabled).toBe(true);
  });

  it("a tab switch during a delayed save keeps the pending-save guard alive", async () => {
    // P2 regression (D-039): the pending-save COUNT lived in SpecsTab, so
    // leaving the tab reset it to zero while a save was genuinely in flight —
    // back on Specs, Save was immediately clickable and a late first
    // response could clear a dirty draft while a second save was pending.
    let resolveSave!: (a: Artifact) => void;
    vi.mocked(api.saveArtifact).mockImplementation(
      () => new Promise<Artifact>((r) => { resolveSave = r; }),
    );

    render(<TabSwitch />);
    const textarea = await openEditor();
    fireEvent.change(textarea, { target: { value: "editing while the server is slow" } });
    fireEvent.click(button(/Save as new version/));
    await waitFor(() => expect(api.saveArtifact).toHaveBeenCalledTimes(1));

    fireEvent.click(button(/toggle-specs/)); // LEAVE the tab mid-save
    await waitFor(() => expect(screen.queryByRole("button", { name: /Product Requirements/ })).toBeNull());
    fireEvent.click(button(/toggle-specs/)); // come back while STILL pending

    // On remount the save is still in flight (counter lives above the tab):
    // Save renders disabled, Approve is blocked by the pending save.
    fireEvent.click(await screen.findByRole("button", { name: /Product Requirements/ }));
    expect(await screen.findByDisplayValue("editing while the server is slow")).toBeTruthy();
    expect(button(/Saving/).disabled).toBe(true);
    expect(screen.getByText(/a save is still in flight/)).toBeTruthy();
    expect(button(/Approve & generate build prompt/).disabled).toBe(true);

    // Save cannot be clicked into a second overlapping request.
    fireEvent.click(button(/Saving/));
    expect(api.saveArtifact).toHaveBeenCalledTimes(1);

    // Only the delayed response resolves everything.
    await act(async () => { resolveSave({ ...ARTIFACT, current_version: 2 }); });
    await waitFor(() => expect(button(/Save as new version/).disabled).toBe(false));
    await waitFor(() => expect(button(/Approve & generate build prompt/).disabled).toBe(false));
  });
});

const PR = { ...ARTIFACT, content: "server text" };
const AC = {
  slug: "acceptance-criteria.json",
  title: "Acceptance Criteria",
  current_version: 1,
  content: '{"id": "ac1", "criterion": "x"}',
  updated_at: "2026-01-01T00:00:00Z",
};

describe("SpecsTab restart (Start over) flow", () => {
  // Mirrors the REAL ProjectPage restart handler (code review finding: the
  // harness used to bump `version` while the page never did, so tests passed
  // on behaviour the app lacks). The real mechanism is the KEY: bumping the
  // epoch remounts SpecsTab, which refetches and starts from clean local
  // state.
  function RestartHarness({ state = "DISCOVERY" }: { state?: ProjectState }) {
    const page = useProjectPageState();
    const [version, setVersion] = useState(0);
    const [show, setShow] = useState(true);
    return (
      <>
        <button onClick={() => setShow((s) => !s)}>toggle-specs</button>
        <button onClick={() => setVersion((v) => v + 1)}>bump</button>
        <button
          onClick={() => page.gen.resetGeneration()}
        >
          restart
        </button>
        {show && (
          <SpecsTab
            key={page.gen.generationKey}
            state={state}
            version={version}
            onAction={() => {}}
            {...page.props}
          />
        )}
      </>
    );
  }

  it("restart re-seeds the selected artifact's draft from ITS OWN regenerated content", async () => {
    // P2 regression (D-040): drafts were cleared but the selection survived;
    // the refetch initialized the selected slug with the FIRST artifact's
    // content — with AC first in the response, the requirements editor showed
    // acceptance criteria, and Save wrote that text into the requirements doc.
    vi.mocked(api.artifacts).mockResolvedValue([PR, AC]);
    render(<RestartHarness />);
    fireEvent.click(await screen.findByRole("button", { name: /Product Requirements/ }));
    expect(await screen.findByDisplayValue("server text")).toBeTruthy();

    // "Start over": regenerated artifacts arrive with acceptance criteria first.
    // The remounted tab (key change) starts fresh: selection falls back to the
    // FIRST artifact. Opening Product Requirements must seed from ITS copy —
    // pre-fix the stale selection got the first artifact's text instead.
    vi.mocked(api.artifacts).mockResolvedValue([
      { ...AC, content: "regenerated criteria" },
      { ...PR, content: "regenerated requirements" },
    ]);
    fireEvent.click(button(/restart/));

    expect(await screen.findByDisplayValue("regenerated criteria")).toBeTruthy(); // AC now selected
    fireEvent.click(button(/Product Requirements/));
    const textarea = await screen.findByDisplayValue("regenerated requirements");
    expect(textarea).toBeTruthy(); // NOT the acceptance-criteria content

    fireEvent.change(textarea, { target: { value: "regenerated requirements + edit" } });
    fireEvent.click(button(/Save as new version/));
    await waitFor(() =>
      expect(api.saveArtifact).toHaveBeenCalledWith(
        "p1", "product-requirements.md", "regenerated requirements + edit",
      ),
    );
  });

  it("restart resets the selection when the selected slug no longer exists", async () => {
    vi.mocked(api.artifacts).mockResolvedValue([PR, AC]);
    render(<RestartHarness />);
    fireEvent.click(await screen.findByRole("button", { name: /Acceptance Criteria/ }));
    expect(await screen.findByDisplayValue(AC.content)).toBeTruthy();

    // The regenerated spec has no acceptance-criteria artifact at all.
    vi.mocked(api.artifacts).mockResolvedValue([{ ...PR, content: "regenerated requirements" }]);
    fireEvent.click(button(/restart/));

    // Selection moved to the surviving artifact — no ghost draft for the dead
    // slug (which would block approval forever), no wrong-content seeding.
    const textarea = await screen.findByDisplayValue("regenerated requirements");
    expect(textarea).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Acceptance Criteria/ })).toBeNull();

    // Pre-fix the (stale) selection stayed "acceptance-criteria.json" with
    // PR text seeded under that dead slug — a Save would have written into a
    // nonexistent artifact. Post-fix the save targets the surviving slug.
    fireEvent.change(textarea, { target: { value: "regenerated requirements + edit" } });
    fireEvent.click(button(/Save as new version/));
    await waitFor(() =>
      expect(api.saveArtifact).toHaveBeenCalledWith(
        "p1", "product-requirements.md", "regenerated requirements + edit",
      ),
    );
  });

  it("a save response that lands after restart is discarded", async () => {
    // P2 regression (D-041): the delayed save response used to update the
    // cleared/regenerated drafts — throwing on the missing draft or stamping
    // the regenerated one with the pre-restart version number.
    let resolveSave!: (a: Artifact) => void;
    vi.mocked(api.saveArtifact).mockImplementation(
      () => new Promise<Artifact>((r) => { resolveSave = r; }),
    );

    render(<RestartHarness state="AWAITING_APPROVAL" />);
    fireEvent.click(await screen.findByRole("button", { name: /Product Requirements/ }));
    const textarea = await screen.findByDisplayValue("server text");
    fireEvent.change(textarea, { target: { value: "typing before restart" } });
    fireEvent.click(button(/Save as new version/));
    await waitFor(() => expect(api.saveArtifact).toHaveBeenCalledTimes(1));

    // Start over mid-save: drafts cleared, epoch bumped (remount), artifacts
    // regenerate; the remounted tab lands on the review panel again.
    vi.mocked(api.artifacts).mockResolvedValue([{ ...PR, content: "fresh regenerated spec" }]);
    fireEvent.click(button(/restart/));
    fireEvent.click(await screen.findByRole("button", { name: /Product Requirements/ }));
    expect(await screen.findByDisplayValue("fresh regenerated spec")).toBeTruthy();

    // The OLD response lands: old content, version 2 of a deleted artifact.
    await act(async () => {
      resolveSave({ ...PR, current_version: 2, content: "typing before restart" });
    });

    // Nothing from the dead generation is applied: the regenerated draft is
    // untouched and clean, no error surfaced, Save works again.
    const fresh = await screen.findByDisplayValue("fresh regenerated spec");
    expect(screen.queryByText(/unsaved changes/)).toBeNull();
    await waitFor(() => expect(button(/Save as new version/).disabled).toBe(false));
    expect(button(/Approve & generate build prompt/).disabled).toBe(false);
    // A new save after restart still behaves normally (fresh v1-based draft).
    fireEvent.change(fresh, { target: { value: "fresh regenerated spec + edit" } });
    fireEvent.click(button(/Save as new version/));
    await waitFor(() =>
      expect(api.saveArtifact).toHaveBeenCalledTimes(2),
    );
  });

  it("a save response across a tab switch AND restart cannot touch the new drafts", async () => {
    // P2 regression (D-042): the generation check lived in SpecsTab. Leave
    // the tab mid-save, restart while away, return — the OLD callback's
    // component-local view had frozen at the pre-restart generation, so its
    // check passed and it restamped the NEW draft through the shared setter.
    // The check now runs in ProjectPage, where the drafts (and the epoch
    // truth) live.
    let resolveSave!: (a: Artifact) => void;
    vi.mocked(api.saveArtifact).mockImplementation(
      () => new Promise<Artifact>((r) => { resolveSave = r; }),
    );
    vi.mocked(api.artifacts).mockResolvedValue([{ ...PR, content: "server text" }]);

    render(<RestartHarness state="AWAITING_APPROVAL" />);
    fireEvent.click(await screen.findByRole("button", { name: /Product Requirements/ }));
    const textarea = await screen.findByDisplayValue("server text");
    fireEvent.change(textarea, { target: { value: "typing before restart" } });
    fireEvent.click(button(/Save as new version/));
    await waitFor(() => expect(api.saveArtifact).toHaveBeenCalledTimes(1));

    fireEvent.click(button(/toggle-specs/)); // LEAVE Specs mid-save
    vi.mocked(api.artifacts).mockResolvedValue([{ ...PR, content: "fresh regenerated spec" }]);
    fireEvent.click(button(/restart/)); // Start over while away
    fireEvent.click(button(/toggle-specs/)); // come back, save still pending
    fireEvent.click(await screen.findByRole("button", { name: /Product Requirements/ }));
    expect(await screen.findByDisplayValue("fresh regenerated spec")).toBeTruthy();

    // The pre-restart response lands after everything has moved on.
    await act(async () => {
      resolveSave({ ...PR, current_version: 2, content: "typing before restart" });
    });

    // Nothing is applied: the regenerated draft stays clean, unblocked.
    expect(await screen.findByDisplayValue("fresh regenerated spec")).toBeTruthy();
    expect(screen.queryByText(/unsaved changes/)).toBeNull();
    await waitFor(() => expect(button(/Save as new version/).disabled).toBe(false));
    await waitFor(() => expect(button(/Approve & generate build prompt/).disabled).toBe(false));
  });

  it("an artifact response from before restart cannot seed the cleared drafts", async () => {
    // P2 regression (D-043): a fetch issued before "Start over" landed after
    // the drafts were cleared and re-seeded them with pre-restart text. The
    // regenerated artifact has the SAME slug and version number, so
    // refetchDrafts saw "no newer version" and kept the stale draft — the
    // operator would have saved old text into the regenerated spec.
    const resolvers: Array<(a: Artifact[]) => void> = [];
    vi.mocked(api.artifacts).mockImplementation(
      () => new Promise<Artifact[]>((r) => { resolvers.push(r); }),
    );

    render(<RestartHarness />);
    expect(resolvers).toHaveLength(1);
    await act(async () => { resolvers.shift()!([{ ...PR, content: "server text" }]); });
    expect(await screen.findByDisplayValue("server text")).toBeTruthy();

    fireEvent.click(button(/bump/)); // a WS-style refetch is now in flight...
    expect(resolvers).toHaveLength(1); // shift() consumed the mount fetch
    fireEvent.click(button(/restart/)); // ...when Start over clears the drafts
    expect(resolvers).toHaveLength(2); // + the regeneration refetch

    // The PRE-RESTART response lands first: stale v1 text, same slug/version.
    await act(async () => { resolvers.shift()!([{ ...PR, content: "server text" }]); });
    expect(screen.queryByDisplayValue("server text")).toBeNull(); // discarded outright

    // The regenerated response lands.
    await act(async () => { resolvers.shift()!([{ ...PR, content: "fresh regenerated spec" }]); });
    const textarea = await screen.findByDisplayValue("fresh regenerated spec");

    fireEvent.change(textarea, { target: { value: "fresh regenerated spec + edit" } });
    fireEvent.click(button(/Save as new version/));
    await waitFor(() =>
      expect(api.saveArtifact).toHaveBeenCalledWith(
        "p1", "product-requirements.md", "fresh regenerated spec + edit",
      ),
    );
  });

  it("a late save from a dead generation cannot release the current save's block", async () => {
    // P1 regression (finding 4): the finally-block endSave used to run even
    // for dead generations. Reset zeroes the count; a dead response then
    // decrements the LIVE generation's pending save — Save/Approve re-open
    // while a save is genuinely in flight, undoing D-039.
    let resolveOld!: (a: Artifact) => void;
    let resolveLive!: (a: Artifact) => void;
    vi.mocked(api.saveArtifact)
      .mockImplementationOnce(() => new Promise<Artifact>((r) => { resolveOld = r; }))
      .mockImplementationOnce(() => new Promise<Artifact>((r) => { resolveLive = r; }));
    vi.mocked(api.artifacts).mockResolvedValue([{ ...PR, content: "server text" }]);

    render(<RestartHarness state="AWAITING_APPROVAL" />);
    fireEvent.click(await screen.findByRole("button", { name: /Product Requirements/ }));
    const textarea = await screen.findByDisplayValue("server text");
    fireEvent.change(textarea, { target: { value: "old generation typing" } });
    fireEvent.click(button(/Save as new version/));
    await waitFor(() => expect(api.saveArtifact).toHaveBeenCalledTimes(1)); // old-gen save in flight

    vi.mocked(api.artifacts).mockResolvedValue([{ ...PR, content: "fresh regenerated spec" }]);
    fireEvent.click(button(/restart/)); // resets drafts, count, and the generation
    fireEvent.click(await screen.findByRole("button", { name: /Product Requirements/ }));
    const fresh = await screen.findByDisplayValue("fresh regenerated spec");
    fireEvent.change(fresh, { target: { value: "fresh regenerated spec + edit" } });
    fireEvent.click(button(/Save as new version/));
    await waitFor(() => expect(api.saveArtifact).toHaveBeenCalledTimes(2)); // live save in flight

    // The DEAD response lands first: it must not touch the live counter.
    await act(async () => { resolveOld({ ...PR, current_version: 2, content: "old generation typing" }); });
    expect(button(/Saving/).disabled).toBe(true); // live save still in flight
    expect(screen.getByText(/a save is still in flight/)).toBeTruthy();
    expect(button(/Approve & generate build prompt/).disabled).toBe(true);

    // Only the LIVE response releases the block.
    await act(async () => { resolveLive({ ...PR, current_version: 2 }); });
    await waitFor(() => expect(button(/Save as new version/).disabled).toBe(false));
    await waitFor(() => expect(button(/Approve & generate build prompt/).disabled).toBe(false));
  });
});
