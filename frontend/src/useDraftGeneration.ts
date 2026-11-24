import { useCallback, useRef, useState } from "react";
import { DraftGeneration } from "./specDraft";

/**
 * The single source of truth for which draft state async responses belong to
 * (code review finding: duplicating the epoch logic in test harnesses let
 * harness-only bugs ship; and keeping it component-local let an unmounted
 * child pass its own stale check). Rules:
 *
 *  - `projectId` is PART of the generation, so a project switch needs no
 *    epoch bump. The ref syncs during RENDER — before any effect runs — so
 *    the child's mount effect (React runs child effects before parent
 *    effects) already captures the NEW project id and its responses survive.
 *  - `resetGeneration()` bumps the epoch SYNCHRONOUSLY (ref first, state
 *    second): a response landing in the same tick as a reset must already
 *    fail the check, not slip through until the next render.
 *  - Epoch bumps also remount keyed children (key = projectId + epoch), so
 *    restart cannot leave stale local UI state (selection, review, history)
 *    behind, and the fresh mount refetches without a version bump.
 */
export function useDraftGeneration(projectId: string) {
  const [draftsEpoch, setDraftsEpoch] = useState(0);
  const ref = useRef<DraftGeneration>({ projectId, draftsEpoch });
  ref.current = { projectId, draftsEpoch };

  const captureGeneration = useCallback(() => ref.current, []);

  const isCurrentGeneration = useCallback((g: DraftGeneration) => {
    const now = ref.current;
    return now.projectId === g.projectId && now.draftsEpoch === g.draftsEpoch;
  }, []);

  const resetGeneration = useCallback(() => {
    const next = ref.current.draftsEpoch + 1;
    ref.current = { ...ref.current, draftsEpoch: next }; // synchronous (finding 3)
    setDraftsEpoch(next);
  }, []);

  return { draftsEpoch, captureGeneration, isCurrentGeneration, resetGeneration };
}
