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

/** Approval is blocked while the operator has not finished looking at what
 *  they are approving: a dirty draft, an in-flight save, or an unresolved
 *  edit conflict (D-029). Returns the reason, or null when approval is clear. */
export function approvalBlocked(p: DraftPolicy, saving: boolean): string | null {
  if (saving) return "a save is still in flight";
  if (p.dirty) return "you have unsaved edits";
  if (p.serverNewer > 0) return "the server changed while your draft is unresolved";
  return null;
}

/** The artifact version map the UI is showing (slug -> current_version),
 *  mirroring the backend's eligible-spec rule: never the handoff prompt,
 *  never an empty (v0) artifact. Sent with APPROVED so the server can refuse
 *  to approve anything the operator did not review (D-029). */
export function reviewedVersionMap(
  artifacts: { slug: string; current_version: number }[],
): Record<string, number> {
  const out: Record<string, number> = {};
  for (const a of artifacts) {
    if (a.slug !== "handoff-prompt.md" && a.current_version > 0) out[a.slug] = a.current_version;
  }
  return out;
}
