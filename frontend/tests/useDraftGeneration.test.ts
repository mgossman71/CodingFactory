// @vitest-environment jsdom
/**
 * Tests for the SHARED generation hook (code review finding: harnesses that
 * re-declare the epoch logic test a copy, and the copy hid two real bugs —
 * the child-before-parent effect ordering on project switch, and the
 * render-only ref update that let same-tick responses through).
 */
import { describe, expect, it } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useDraftGeneration } from "../src/useDraftGeneration";

describe("useDraftGeneration", () => {
  it("resetGeneration invalidates captures synchronously (no render gap)", () => {
    const { result } = renderHook(() => useDraftGeneration("p1"));
    const captured = result.current.captureGeneration();
    act(() => {
      result.current.resetGeneration();
    });
    // Same tick, before any re-render: a response landing now must already
    // fail — the ref updates with the state, not after it.
    expect(result.current.isCurrentGeneration(captured)).toBe(false);
    expect(result.current.isCurrentGeneration(result.current.captureGeneration())).toBe(true);
  });

  it("a project switch invalidates the old project without an epoch bump", () => {
    // projectId is PART of the generation, so ProjectPage never bumps the
    // epoch from its [id] effect — the remounted child's effect (which runs
    // FIRST) already captures the new id and keeps its responses.
    const { result, rerender } = renderHook(
      ({ pid }: { pid: string }) => useDraftGeneration(pid),
      { initialProps: { pid: "p1" } },
    );
    const captured = result.current.captureGeneration();
    rerender({ pid: "p2" });

    expect(result.current.isCurrentGeneration(captured)).toBe(false); // A's in-flight die
    const fresh = result.current.captureGeneration(); // child effect captures now
    expect(fresh.projectId).toBe("p2");
    expect(result.current.isCurrentGeneration(fresh)).toBe(true); // and survive
  });

  it("restart bumps the epoch; the project id stays valid", () => {
    const { result } = renderHook(() => useDraftGeneration("p1"));
    const before = result.current.captureGeneration();
    act(() => {
      result.current.resetGeneration();
    });
    expect(result.current.isCurrentGeneration(before)).toBe(false);
    const after = result.current.captureGeneration();
    expect(after).toEqual({ projectId: "p1", draftsEpoch: 1 });
  });
});
