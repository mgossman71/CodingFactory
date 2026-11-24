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

/** The server accepted `savedContent` as `newVersion`. Code review (P2): only
 *  clear the dirty flag when the draft STILL equals what was saved — edits
 *  typed while the save was in flight must stay dirty (and approval-blocked). */
export function saved(p: DraftPolicy, newVersion: number, savedContent: string): DraftPolicy {
  return { ...p, baseVersion: newVersion, dirty: p.draft !== savedContent, serverNewer: 0 };
}

/**
 * Drafts are tracked PER ARTIFACT (code review P2): with one shared draft,
 * selecting another artifact silently discarded unsaved typing and cleared the
 * dirty flag that blocks approval. These map-aware helpers let the editor hold
 * every open artifact's draft at once.
 */
export function openDraft(
  drafts: Record<string, DraftPolicy>,
  slug: string,
  server: ServerArtifact,
): Record<string, DraftPolicy> {
  // Selecting never overwrites an existing draft — switching artifacts must
  // never lose typing.
  return slug in drafts ? drafts : { ...drafts, [slug]: selectArtifact(server) };
}

export function editDraft(
  drafts: Record<string, DraftPolicy>,
  slug: string,
  draft: string,
): Record<string, DraftPolicy> {
  return { ...drafts, [slug]: edited(drafts[slug], draft) };
}

/** A refetch lands for ALL artifacts: every open draft goes through the
 *  per-artifact policy (dirty preserved, divergence flagged). */
export function refetchDrafts(
  drafts: Record<string, DraftPolicy>,
  servers: Record<string, ServerArtifact | undefined>,
): Record<string, DraftPolicy> {
  const out: Record<string, DraftPolicy> = {};
  for (const [slug, p] of Object.entries(drafts)) out[slug] = refetched(p, servers[slug]);
  return out;
}

/** Approval is blocked while ANY open artifact has unfinished business — not
 *  just the selected one (D-029 + P2 review). Returns the reason, or null. */
export function approvalBlocked(drafts: Record<string, DraftPolicy>, saving: boolean): string | null {
  if (saving) return "a save is still in flight";
  for (const [slug, p] of Object.entries(drafts)) {
    if (p.dirty) return `you have unsaved edits in ${slug}`;
  }
  for (const [slug, p] of Object.entries(drafts)) {
    if (p.serverNewer > 0) return `the server changed in ${slug} while your draft is unresolved`;
  }
  return null;
}

/**
 * Pending-save accounting (P2 review): a shared boolean meant whichever save
 * responded first cleared "saving" even while another was still in flight —
 * re-enabling Approve mid-save. Track the COUNT of in-flight saves instead;
 * the Save button is also disabled while saving, so requests serialize.
 */
export function beginSave(pending: number): number {
  return pending + 1;
}

export function endSave(pending: number): number {
  return Math.max(0, pending - 1);
}

/**
 * Which artifact the editor should hold after a refresh (P2 review): keep the
 * current selection only when an artifact with that slug still exists;
 * otherwise fall back to the first non-handoff artifact. Initializing the
 * selected slug's draft from a DIFFERENT artifact's content (the old
 * `selected || first` shortcut) let a post-restart editor show acceptance
 * criteria inside the requirements document — and Save would write it there.
 */
export function resolveSelectedSlug(slugs: string[], selected: string): string {
  if (selected && slugs.includes(selected)) return selected;
  return slugs.find((s) => s !== "handoff-prompt.md") ?? slugs[0] ?? "";
}

/**
 * A save response updates ONLY an existing draft for that slug (P2 review):
 * after "Start over" cleared the map, the late callback used to resurrect a
 * draft from `undefined` (throwing) or stamp a regenerated draft with the
 * old version number. Returns the map unchanged when the draft is gone.
 */
export function applySaved(
  drafts: Record<string, DraftPolicy>,
  slug: string,
  newVersion: number,
  savedContent: string,
): Record<string, DraftPolicy> {
  const p = drafts[slug];
  if (!p) return drafts;
  return { ...drafts, [slug]: saved(p, newVersion, savedContent) };
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
