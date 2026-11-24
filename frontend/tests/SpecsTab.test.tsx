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
import { useCallback, useRef, useState } from "react";
import { act } from "react";
import SpecsTab from "../src/components/SpecsTab";
import { api, Artifact, ProjectState, Review } from "../src/api";
import { DraftGeneration, DraftPolicy } from "../src/specDraft";

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
  assumptions: [],
  decisions: [],
  decision_history: null,
  interpretations: [],
  unresolved: [],
  acceptance_criteria: [],
  sources: [{ slug: "product-requirements.md", title: "Product Requirements", version: 1, version_id: "vid-1" }],
};

function useProjectPageState() {
  // Mirrors ProjectPage's ownership: drafts, pending-save accounting, AND the
  // draft generation (capture/check) all outlive the SpecsTab component
  // (D-036/D-039/D-042).
  const [drafts, setDrafts] = useState<Record<string, DraftPolicy>>({});
  const [pendingSaves, setPendingSaves] = useState(0);
  const [draftsEpoch, setDraftsEpoch] = useState(0);
  const genRef = useRef<DraftGeneration>({ projectId: "p1", draftsEpoch });
  genRef.current = { projectId: "p1", draftsEpoch };
  const captureGeneration = useCallback(() => genRef.current, []);
  const isCurrentGeneration = useCallback((g: DraftGeneration) => {
    const now = genRef.current;
    return now.projectId === g.projectId && now.draftsEpoch === g.draftsEpoch;
  }, []);
  return {
    drafts, setDrafts, pendingSaves, setPendingSaves,
    setDraftsEpoch, captureGeneration, isCurrentGeneration,
  };
}

function Wrapper({ state = "AWAITING_APPROVAL" }: { state?: ProjectState }) {
  const page = useProjectPageState();
  return (
    <SpecsTab
      projectId="p1"
      state={state}
      version={0}
      onAction={() => {}}
      {...page}
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
            projectId="p1"
            state="AWAITING_APPROVAL"
            version={0}
            onAction={() => {}}
            {...page}
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
  // Mirrors ProjectPage's restart handler: clear drafts, bump the epoch,
  // bump `version` to trigger the refetch of regenerated artifacts. Also a
  // tab toggle and a bare version bump (WS-style refetch) for lifecycle
  // races.
  function RestartHarness({ state = "DISCOVERY" }: { state?: ProjectState }) {
    const page = useProjectPageState();
    const [version, setVersion] = useState(0);
    const [show, setShow] = useState(true);
    return (
      <>
        <button onClick={() => setShow((s) => !s)}>toggle-specs</button>
        <button onClick={() => setVersion((v) => v + 1)}>bump</button>
        <button
          onClick={() => {
            page.setDrafts({});
            page.setDraftsEpoch((n) => n + 1);
            setVersion((v) => v + 1);
          }}
        >
          restart
        </button>
        {show && (
          <SpecsTab
            projectId="p1"
            state={state}
            version={version}
            onAction={() => {}}
            {...page}
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
    vi.mocked(api.artifacts).mockResolvedValue([
      { ...AC, content: "regenerated criteria" },
      { ...PR, content: "regenerated requirements" },
    ]);
    fireEvent.click(button(/restart/));

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

    // Start over mid-save: drafts cleared, epoch bumped, artifacts regenerate.
    vi.mocked(api.artifacts).mockResolvedValue([{ ...PR, content: "fresh regenerated spec" }]);
    fireEvent.click(button(/restart/));
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
});
