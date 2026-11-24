/**
 * Spec-editor draft policy (review finding, P1).
 *
 * Project polling refetches artifacts every ~5s; the editor used to replace
 * the textarea with server content mid-typing, erasing unsaved work. These
 * pure helpers define the refresh contract:
 *
 *  - a refetch NEVER overwrites a dirty draft;
 *  - if the server moved to a newer version while the draft is dirty, flag it
 *    (serverNewer) so the UI can say so before saving;
 *  - a clean draft adopts the newer server version automatically.
 */

export interface DraftPolicy {
  draft: string;
  /** the server version the draft was loaded from */
  baseVersion: number;
  /** the user has edits that were never saved */
  dirty: boolean;
  /** the server's current version when it moved past baseVersion while dirty (0 = no divergence) */
  serverNewer: number;
}

export interface ServerArtifact {
  current_version: number;
  content: string;
}

export const freshDraft: DraftPolicy = { draft: "", baseVersion: 0, dirty: false, serverNewer: 0 };

/** Selecting an artifact adopts the server copy. */
export function selectArtifact(server: ServerArtifact): DraftPolicy {
  return { draft: server.content, baseVersion: server.current_version, dirty: false, serverNewer: 0 };
}

/** The user typed. */
export function edited(p: DraftPolicy, draft: string): DraftPolicy {
  return { ...p, draft, dirty: true };
}

/** Pulling an older version from history into the editor is an edit too. */
export function restoredFromHistory(p: DraftPolicy, content: string): DraftPolicy {
  return { ...p, draft: content, dirty: true };
}

/** A refetch (WS event / 5s poll) landed. Preserve unsaved work (see header). */
export function refetched(p: DraftPolicy, server: ServerArtifact | undefined): DraftPolicy {
  if (!server || server.current_version === p.baseVersion) return p;
  if (!p.dirty) return selectArtifact(server);
  return { ...p, serverNewer: server.current_version };
}

/** The server accepted the draft as `newVersion` (a new artifact version). */
export function saved(p: DraftPolicy, newVersion: number): DraftPolicy {
  return { ...p, baseVersion: newVersion, dirty: false, serverNewer: 0 };
}
