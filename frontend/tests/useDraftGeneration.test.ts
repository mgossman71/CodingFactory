// @vitest-environment jsdom
/**
 * Tests for the SHARED generation hook (D-045). The generation is stored with
 * the drafts and pending-save count it scopes. Writes are generation-scoped
 * updaters, so a stale write is a no-op whether it is queued before or after
 * the reset, and no ref timing is involved.
 */
import { describe, expect, it } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useDraftGeneration } from "../src/useDraftGeneration";
import { freshDraft } from "../src/specDraft";

const seed = { "a.md": { ...freshDraft, draft: "old text" } };

describe("useDraftGeneration", () => {
  it("a write queued in the same batch as a reset cannot land, in either order", () => {
    const { result } = renderHook(() => useDraftGeneration("p1"));
    const old = result.current.generation;
    act(() => {
      result.current.resetGeneration();
      result.current.updateDrafts(old, seed); // AFTER the reset: generation mismatch
      result.current.updatePendingSaves(old, (n) => n + 1);
    });
    expect(result.current.drafts).toEqual({});
    expect(result.current.pendingSaves).toBe(0);

    const before = result.current.generation;
    act(() => {
      result.current.updateDrafts(before, seed); // BEFORE the reset: wiped by it
      result.current.resetGeneration();
    });
    expect(result.current.drafts).toEqual({});
  });

  it("epochs are never reused across project switches (A -> B -> A)", () => {
    const { result, rerender } = renderHook(
      ({ pid }: { pid: string }) => useDraftGeneration(pid),
      { initialProps: { pid: "p1" } },
    );
    const firstVisit = result.current.generation;
    act(() => result.current.updateDrafts(firstVisit, seed));
    act(() => result.current.updatePendingSaves(firstVisit, 1));

    rerender({ pid: "p2" });
    // Reset during render: the switched-to project never renders with A's drafts.
    expect(result.current.generation.projectId).toBe("p2");
    expect(result.current.drafts).toEqual({});
    expect(result.current.pendingSaves).toBe(0);

    rerender({ pid: "p1" });
    const secondVisit = result.current.generation;
    expect(secondVisit).toEqual({ projectId: "p1", draftsEpoch: 2 });
    expect(result.current.generationKey).toBe("p1:2");

    act(() => result.current.updatePendingSaves(secondVisit, 1)); // live save
    act(() => result.current.updatePendingSaves(firstVisit, 0)); // first visit's late finally
    expect(result.current.pendingSaves).toBe(1); // untouched
  });

  it("writes for the live generation apply", () => {
    const { result } = renderHook(() => useDraftGeneration("p1"));
    act(() => result.current.updateDrafts(result.current.generation, seed));
    expect(result.current.drafts).toEqual(seed);
    act(() => result.current.resetGeneration());
    expect(result.current.generation).toEqual({ projectId: "p1", draftsEpoch: 1 });
    expect(result.current.drafts).toEqual({});
  });
});
