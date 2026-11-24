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
import { DraftPolicy } from "../src/specDraft";

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

function Wrapper({ state = "AWAITING_APPROVAL" }: { state?: ProjectState }) {
  // Mirrors ProjectPage's ownership of drafts.
  const [drafts, setDrafts] = useState<Record<string, DraftPolicy>>({});
  return (
    <SpecsTab
      projectId="p1"
      state={state}
      version={0}
      onAction={() => {}}
      drafts={drafts}
      setDrafts={setDrafts}
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
  it("unmount + remount (Discussion/Activity tab switch) preserves drafts and the approval block", async () => {
    function TabSwitch() {
      // Mirrors ProjectPage: drafts outlive the SpecsTab component.
      const [drafts, setDrafts] = useState<Record<string, DraftPolicy>>({});
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
              drafts={drafts}
              setDrafts={setDrafts}
            />
          )}
        </>
      );
    }

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
});
