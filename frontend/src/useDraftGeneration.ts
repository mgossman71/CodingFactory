import { SetStateAction, useCallback, useState } from "react";
import { DraftGeneration, DraftPolicy } from "./specDraft";

type Drafts = Record<string, DraftPolicy>;

/** Drafts and pending saves stored TOGETHER with the generation they belong to. */
interface DraftScope {
  generation: DraftGeneration;
  drafts: Drafts;
  pendingSaves: number;
}

const freshScope = (generation: DraftGeneration): DraftScope => ({ generation, drafts: {}, pendingSaves: 0 });

const sameGeneration = (a: DraftGeneration, b: DraftGeneration) =>
  a.projectId === b.projectId && a.draftsEpoch === b.draftsEpoch;

const apply = <T,>(action: SetStateAction<T>, prev: T): T =>
  typeof action === "function" ? (action as (p: T) => T)(prev) : action;

/**
 * The single source of truth for spec drafts and which generation they
 * belong to (D-045, second /code-review batch). Rules:
 *
 *  - The generation is STATE, stored with the drafts and pending-save count
 *    it scopes. No ref, so nothing is written during render, and a render
 *    React throws away cannot leave a generation that never committed
 *    marked as live.
 *  - Every write names the generation it was issued in and runs as a state
 *    UPDATER that is a no-op when the stored generation differs. The check
 *    runs against the exact state the update applies to, so timing cannot
 *    matter: a stale update queued before a reset is wiped by the reset; one
 *    queued after it finds a different generation and does nothing.
 *  - The epoch is monotonic across project switches as well as restarts
 *    (A -> B -> A gives epochs 0, 1, 2). A request from an earlier visit to the
 *    same project can never match the current one. Before this change A's
 *    epoch was reused, so a late save landed on the new visit's drafts and
 *    its finally-block decremented the live pending-save count (D-039
 *    regression).
 *  - A project switch resets the scope DURING RENDER (React's derived-state
 *    pattern), so no child ever renders, or runs an effect, with drafts from
 *    another project. The old [id] effect ran after child effects.
 */
export function useDraftGeneration(projectId: string) {
  const [scope, setScope] = useState<DraftScope>(() => freshScope({ projectId, draftsEpoch: 0 }));

  let current = scope;
  if (scope.generation.projectId !== projectId) {
    current = freshScope({ projectId, draftsEpoch: scope.generation.draftsEpoch + 1 });
    setScope((s) => (s.generation.projectId === projectId ? s : current));
  }

  const updateDrafts = useCallback((g: DraftGeneration, action: SetStateAction<Drafts>) => {
    setScope((s) => (sameGeneration(s.generation, g) ? { ...s, drafts: apply(action, s.drafts) } : s));
  }, []);

  const updatePendingSaves = useCallback((g: DraftGeneration, action: SetStateAction<number>) => {
    setScope((s) => (sameGeneration(s.generation, g) ? { ...s, pendingSaves: apply(action, s.pendingSaves) } : s));
  }, []);

  /** "Start over": new epoch, empty drafts, zero pending saves, in one update. */
  const resetGeneration = useCallback(() => {
    setScope((s) => freshScope({ ...s.generation, draftsEpoch: s.generation.draftsEpoch + 1 }));
  }, []);

  const { generation, drafts, pendingSaves } = current;
  return {
    generation,
    /** One identity for SpecsTab's key, so the key, the prop and every check agree. */
    generationKey: `${generation.projectId}:${generation.draftsEpoch}`,
    drafts,
    pendingSaves,
    updateDrafts,
    updatePendingSaves,
    resetGeneration,
  };
}
