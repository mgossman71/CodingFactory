// @vitest-environment jsdom
/**
 * Page-level tests against the REAL ProjectPage under a router (D-045, second
 * /code-review batch: the old SwitchHarness fed one pid to the hook, the key
 * and the prop, so it could not see the page's route-id vs project.id split
 * or the epoch reuse on A -> B -> A). Only sibling tabs are stubbed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import ProjectPage from "../src/pages/ProjectPage";
import { api, Artifact, ProjectDetail } from "../src/api";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../src/api", () => ({
  api: {
    project: vi.fn(),
    restart: vi.fn(),
    renameProject: vi.fn(),
    artifacts: vi.fn(),
    artifactHistory: vi.fn(),
    review: vi.fn(),
    saveArtifact: vi.fn(),
    approval: vi.fn(),
  },
  AGENT_STYLES: {},
  STATE_COLORS: {},
}));
vi.mock("../src/pages/Dashboard", () => ({ StateBadge: () => null }));
vi.mock("../src/components/DiscussionTab", () => ({ default: () => null }));
vi.mock("../src/components/FeedbackPanel", () => ({ default: () => null }));
vi.mock("../src/components/TasksTab", () => ({ default: () => null }));
vi.mock("../src/components/HandoffTab", () => ({ default: () => null }));
vi.mock("../src/components/ActivityTab", () => ({ default: () => null }));

class FakeWebSocket {
  onmessage: unknown = null;
  onclose: unknown = null;
  close() {}
}

const project = (id: string): ProjectDetail => ({
  id,
  name: `Project ${id}`,
  idea: "idea",
  state: "AWAITING_APPROVAL",
  discovery_round: 1,
  iteration: 0,
  error: null,
  created_at: "2026-01-01T00:00:00Z",
  task_count: 0,
  artifacts: ["product-requirements.md"],
});

const artifact = (content: string): Artifact => ({
  slug: "product-requirements.md",
  title: "Product Requirements",
  current_version: 1,
  content,
  updated_at: "2026-01-01T00:00:00Z",
});

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

function Nav() {
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => navigate("/projects/A")}>go-A</button>
      <button onClick={() => navigate("/projects/B")}>go-B</button>
    </>
  );
}

function renderPage(start = "/projects/A") {
  return render(
    <MemoryRouter initialEntries={[start]}>
      <Nav />
      <Routes>
        <Route path="/projects/:id" element={<ProjectPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

const button = (name: RegExp) => screen.getByRole("button", { name }) as HTMLButtonElement;

async function openSpecsEditor(text: string) {
  fireEvent.click(await screen.findByRole("button", { name: /^Specs/ }));
  fireEvent.click(await screen.findByRole("button", { name: /Product Requirements/ }));
  return screen.findByDisplayValue(text);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("WebSocket", FakeWebSocket);
  vi.mocked(api.project).mockImplementation((id: string) => Promise.resolve(project(id)));
  vi.mocked(api.artifacts).mockImplementation((id: string) => Promise.resolve([artifact(`text of ${id}`)]));
  vi.mocked(api.artifactHistory).mockResolvedValue([]);
  vi.mocked(api.review).mockImplementation((id: string) =>
    Promise.resolve({
      in_scope: [], out_of_scope: [], assumptions: [], decisions: [], decision_history: null,
      interpretations: [], unresolved: [], acceptance_criteria: [],
      sources: [{ slug: "product-requirements.md", title: "Product Requirements", version: 1, version_id: `vid-${id}` }],
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ProjectPage generation boundaries", () => {
  it("A -> B -> A: a save from the first visit cannot release the second visit's save block", async () => {
    // Finding 1: without an epoch bump on switch, both visits to A were {A,0}.
    // The first visit's late save passed the check, and its finally took the
    // live count to 0 while the second visit's save was still in flight.
    const first = deferred<Artifact>();
    const second = deferred<Artifact>();
    vi.mocked(api.saveArtifact)
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise);

    renderPage();
    const editor = await openSpecsEditor("text of A");
    fireEvent.change(editor, { target: { value: "first visit edit" } });
    fireEvent.click(button(/Save as new version/));
    await waitFor(() => expect(api.saveArtifact).toHaveBeenCalledTimes(1));

    fireEvent.click(button(/go-B/));
    await screen.findByText("Project B");
    fireEvent.click(button(/go-A/));
    const again = await openSpecsEditor("text of A");
    fireEvent.change(again, { target: { value: "second visit edit" } });
    fireEvent.click(button(/Save as new version/));
    await waitFor(() => expect(api.saveArtifact).toHaveBeenCalledTimes(2));

    await act(async () => { first.resolve({ ...artifact("first visit edit"), current_version: 2 }); });
    expect(button(/Saving/).disabled).toBe(true); // second visit's save still in flight
    expect(button(/Approve & generate build prompt/).disabled).toBe(true);
    expect(screen.getByDisplayValue("second visit edit")).toBeTruthy(); // not restamped by the dead save

    await act(async () => { second.resolve({ ...artifact("second visit edit"), current_version: 2 }); });
    await waitFor(() => expect(button(/Save as new version/).disabled).toBe(false));
  });

  it("while project B loads, nothing of A stays mounted to leak into B's drafts", async () => {
    // Finding 2/3/5: SpecsTab stayed mounted with A's projectId until
    // api.project(B) resolved, while the generation already said B.
    const bLoad = deferred<ProjectDetail>();
    vi.mocked(api.project).mockImplementation((id: string) =>
      id === "B" ? bLoad.promise : Promise.resolve(project(id)),
    );

    renderPage();
    await openSpecsEditor("text of A");
    vi.mocked(api.artifacts).mockClear();

    fireEvent.click(button(/go-B/));
    // The editor is gone at once, so there is no window for an A save or A fetch.
    expect(screen.queryByDisplayValue("text of A")).toBeNull();
    expect(screen.getByText("Loading…")).toBeTruthy();

    await act(async () => { bLoad.resolve(project("B")); });
    const editor = await openSpecsEditor("text of B");
    expect(editor).toBeTruthy();
    expect(vi.mocked(api.artifacts).mock.calls.every(([id]) => id === "B")).toBe(true);
  });

  it("a late api.project(A) cannot put A back on screen under B's route", async () => {
    // Finding 4: reload had no guard, so a slow A response landing after B
    // rendered A under /projects/B with every request accepted as current.
    const aLoad = deferred<ProjectDetail>();
    vi.mocked(api.project).mockImplementation((id: string) =>
      id === "A" ? aLoad.promise : Promise.resolve(project(id)),
    );

    renderPage();
    fireEvent.click(button(/go-B/));
    expect(await screen.findByText("Project B")).toBeTruthy();

    await act(async () => { aLoad.resolve(project("A")); });
    expect(screen.queryByText("Project A")).toBeNull();
    expect(screen.getByText("Project B")).toBeTruthy();
  });

  it("the real Start over button refetches the regenerated spec and drops a pre-restart save", async () => {
    const save = deferred<Artifact>();
    vi.mocked(api.saveArtifact).mockImplementation(() => save.promise);
    vi.mocked(api.restart).mockResolvedValue(project("A"));
    vi.stubGlobal("confirm", () => true);

    renderPage();
    const editor = await openSpecsEditor("text of A");
    fireEvent.change(editor, { target: { value: "typing before restart" } });
    fireEvent.click(button(/Save as new version/));
    await waitFor(() => expect(api.saveArtifact).toHaveBeenCalledTimes(1));

    vi.mocked(api.artifacts).mockResolvedValue([artifact("regenerated spec")]);
    fireEvent.click(button(/Start over/));
    // No version bump needed: the key change remounts SpecsTab, which refetches.
    // The remount lands on the review panel, so the old editor disappears first.
    await waitFor(() => expect(screen.queryByDisplayValue("typing before restart")).toBeNull());
    fireEvent.click(await screen.findByRole("button", { name: /Product Requirements/ }));
    expect(await screen.findByDisplayValue("regenerated spec")).toBeTruthy();

    await act(async () => { save.resolve({ ...artifact("typing before restart"), current_version: 2 }); });
    expect(screen.getByDisplayValue("regenerated spec")).toBeTruthy();
    expect(screen.queryByText(/unsaved changes/)).toBeNull();
    expect(button(/Save as new version/).disabled).toBe(false);
  });
});

describe("ProjectPage rename", () => {
  it("inline rename calls the API and updates the heading", async () => {
    vi.mocked(api.renameProject).mockImplementation((id, name) =>
      Promise.resolve({ ...project(id), name }),
    );

    renderPage();
    await screen.findByText("Project A");

    fireEvent.click(screen.getByTitle("Rename"));
    const input = screen.getByRole("textbox");
    expect(input.value).toBe("Project A"); // pre-filled

    fireEvent.change(input, { target: { value: "Renamed via UI" } });
    fireEvent.click(button(/^Save$/));

    await waitFor(() => expect(api.renameProject).toHaveBeenCalledWith("A", "Renamed via UI"));
    await screen.findByText("Renamed via UI");
    expect(screen.queryByText("Project A")).toBeNull();
  });

  it("Escape cancels the rename without calling the API", async () => {
    renderPage();
    await screen.findByText("Project A");

    fireEvent.click(screen.getByTitle("Rename"));
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "Never saved" } });
    fireEvent.keyDown(input, { key: "Escape" });

    await waitFor(() => expect(screen.queryByRole("textbox")).toBeNull());
    expect(api.renameProject).not.toHaveBeenCalled();
    expect(screen.getByText("Project A")).toBeTruthy();
  });

  it("Save is disabled while the draft is blank", async () => {
    renderPage();
    await screen.findByText("Project A");

    fireEvent.click(screen.getByTitle("Rename"));
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "   " } });
    expect(button(/^Save$/).disabled).toBe(true);
  });
});
