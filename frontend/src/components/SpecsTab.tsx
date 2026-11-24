import { Dispatch, SetStateAction, useCallback, useEffect, useRef, useState } from "react";
import { api, Artifact, ArtifactVersion, ProjectState, Review } from "../api";
import {
  applySaved,
  approvalBlocked,
  beginSave,
  DraftPolicy,
  editDraft,
  endSave,
  freshDraft,
  openDraft,
  refetchDrafts,
  resolveSelectedSlug,
  restoredFromHistory,
  selectArtifact,
} from "../specDraft";

export default function SpecsTab({
  projectId,
  state,
  version,
  onAction,
  drafts,
  setDrafts,
  pendingSaves,
  setPendingSaves,
  draftsEpoch,
}: {
  projectId: string;
  state: ProjectState;
  version: number;
  onAction: () => void;
  // Drafts, pending-save accounting, AND the drafts generation (epoch) are
  // owned by ProjectPage: leaving the Specs tab (unmount) must not destroy
  // unsaved work or forget a save that is still in flight (P2 reviews,
  // D-036/D-039). Drafts are also tracked PER ARTIFACT: switching artifacts
  // never discards typing, and ANY dirty draft keeps approval blocked.
  // The epoch bumps whenever ProjectPage clears drafts (project change,
  // "Start over") — save responses from an earlier generation are ignored.
  drafts: Record<string, DraftPolicy>;
  setDrafts: Dispatch<SetStateAction<Record<string, DraftPolicy>>>;
  pendingSaves: number;
  setPendingSaves: Dispatch<SetStateAction<number>>;
  draftsEpoch: number;
}) {
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [history, setHistory] = useState<ArtifactVersion[]>([]);
  const [comment, setComment] = useState("");
  const [error, setError] = useState("");
  // Pending saves are counted, not a shared boolean: whichever response lands
  // first cannot re-enable Approve while another save is still in flight (P2),
  // and the count lives in ProjectPage so a tab switch cannot reset it (D-039).
  const saving = pendingSaves > 0;
  const [review, setReview] = useState<Review | null>(null);
  const [staleMap, setStaleMap] = useState(false); // last approve hit a 409
  const [view, setView] = useState<"review" | "editor">("editor");

  // Live identity of the generation this render belongs to — save() captures
  // the generation at click time and compares against this ref when the
  // response lands, so a response from before a drafts reset is discarded.
  const latest = useRef({ projectId, draftsEpoch });
  latest.current = { projectId, draftsEpoch };

  useEffect(() => {
    api.artifacts(projectId).then((a) => {
      setArtifacts(a);
      const servers = Object.fromEntries(a.map((x) => [x.slug, x]));
      setDrafts((d) => refetchDrafts(d, servers)); // never overwrites dirty drafts
      // D-040: the draft is ALWAYS initialized from the artifact that matches
      // the slug being opened — and if the selected slug no longer exists
      // (restart regenerated the spec), selection resets to the first
      // non-handoff artifact instead of adopting someone else's content.
      const target = resolveSelectedSlug(a.map((x) => x.slug), selected);
      if (target && target !== selected) setSelected(target);
      if (target) setDrafts((d) => openDraft(d, target, servers[target]));
    });
  }, [projectId, version]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!selected) return;
    const cur = artifacts.find((a) => a.slug === selected);
    if (cur) setDrafts((d) => openDraft(d, selected, cur)); // existing draft kept as-is
    api.artifactHistory(projectId, selected).then(setHistory);
  }, [selected]); // eslint-disable-line react-hooks/exhaustive-deps

  // The compact review (D-029): one panel over the same versioned artifacts
  // the coding agent will receive. Reloaded at every approval gate and after
  // a 409, so it always shows what a fresh Approve click will freeze.
  const loadReview = useCallback(() => {
    api.review(projectId).then(setReview).catch(() => setReview(null));
  }, [projectId]);

  useEffect(() => {
    if (state === "AWAITING_APPROVAL") loadReview();
    else setReview(null);
  }, [state, version, loadReview]);

  // Land on the compact review when the approval gate opens; editing an
  // artifact switches back to the editor.
  useEffect(() => {
    setView(state === "AWAITING_APPROVAL" ? "review" : "editor");
  }, [state]);

  const current = artifacts.find((a) => a.slug === selected);
  const policy = (selected && drafts[selected]) || freshDraft;

  async function save() {
    if (!selected || !drafts[selected] || pendingSaves > 0) return; // serialized
    const slug = selected;
    const content = drafts[slug].draft; // what is on screen matters, not later typing
    // D-041: capture the generation that initiated this request. If "Start
    // over" (or a project switch) clears the drafts while the response is in
    // flight, the late callback must not resurrect or restamp post-reset
    // drafts — it belongs to a dead generation and is discarded.
    const generation = { projectId, draftsEpoch };
    setPendingSaves(beginSave);
    setError("");
    try {
      const updated = await api.saveArtifact(projectId, slug, content);
      if (latest.current.projectId !== generation.projectId ||
          latest.current.draftsEpoch !== generation.draftsEpoch) return;
      // applySaved: only an EXISTING draft for that slug is updated, and only
      // to `saved()` semantics — dirty clears just when the draft still
      // equals `content`; mid-save typing stays dirty and blocked.
      setDrafts((d) => applySaved(d, slug, updated.current_version, content));
      onAction();
    } catch (e) {
      if (latest.current.projectId !== generation.projectId ||
          latest.current.draftsEpoch !== generation.draftsEpoch) return;
      setError(String(e));
    } finally {
      setPendingSaves(endSave); // the request finished either way
    }
  }

  async function decide(decision: "APPROVED" | "REJECTED" | "REVISION_REQUESTED") {
    setError("");
    try {
      // APPROVED carries the exact artifacts this review rendered: the
      // slug+version map AND the immutable version-row ids. The server
      // rejects (409) anything the operator has not seen — including a
      // pre-"Start over" map, whose names and version numbers would otherwise
      // collide with the regenerated spec.
      const reviewed =
        decision === "APPROVED" && review
          ? Object.fromEntries(review.sources.map((s) => [s.slug, s.version]))
          : undefined;
      const reviewedIds =
        decision === "APPROVED" && review
          ? Object.fromEntries(review.sources.map((s) => [s.slug, s.version_id]))
          : undefined;
      await api.approval(projectId, decision, comment || undefined, reviewed, reviewedIds);
      setStaleMap(false);
      onAction();
    } catch (e) {
      const msg = String(e);
      if (msg.includes("409") && msg.includes("changed since you reviewed")) {
        setStaleMap(true);
        loadReview(); // fresh review; a fresh click is required to approve
      }
      setError(msg);
    }
  }

  if (!artifacts.length)
    return <p className="text-sm text-slate-500">No specifications yet — they appear after discovery completes.</p>;

  const blocked = approvalBlocked(drafts, saving);
  const dirtyCount = Object.values(drafts).filter((p) => p.dirty).length;

  return (
    <div className="grid gap-4 lg:grid-cols-[220px_1fr]">
      <div className="space-y-1">
        {review && (
          <button
            onClick={() => setView("review")}
            className={`block w-full text-left text-sm rounded px-3 py-2 ${
              view === "review" ? "bg-slate-800" : "hover:bg-slate-900 text-slate-300"
            }`}
          >
            Approval review
          </button>
        )}
        {artifacts.map((a) => (
          <button
            key={a.slug}
            onClick={() => {
              setSelected(a.slug);
              setView("editor");
            }}
            className={`block w-full text-left text-sm rounded px-3 py-2 ${
              view === "editor" && selected === a.slug ? "bg-slate-800" : "hover:bg-slate-900 text-slate-300"
            }`}
          >
            {a.title} <span className="text-slate-500">v{a.current_version}</span>
            {drafts[a.slug]?.dirty && <span className="text-amber-400"> • unsaved</span>}
          </button>
        ))}
      </div>

      <div className="space-y-3">
        {review && view === "review" && <ReviewPanel review={review} />}

        {view === "editor" && (
          <>
            {policy.serverNewer > 0 && (
              <div className="rounded border border-amber-700 bg-amber-950/40 p-3 text-sm text-amber-300 space-y-2">
                <p>
                  {selected} changed on the server (v{policy.serverNewer}) while you have unsaved
                  edits based on v{policy.baseVersion}. Your draft is preserved. Saving now creates
                  v{policy.serverNewer + 1} from your draft; the server version stays in history.
                </p>
                {current && (
                  <button
                    onClick={() => setDrafts((d) => ({ ...d, [selected]: selectArtifact(current) }))}
                    className="rounded border border-amber-600 text-amber-200 hover:bg-amber-900/40 px-3 py-1 text-xs"
                  >
                    Discard my edits, load v{policy.serverNewer}
                  </button>
                )}
              </div>
            )}

            <textarea
              value={policy.draft}
              onChange={(e) => selected && setDrafts((d) => editDraft(d, selected, e.target.value))}
              rows={22}
              className="w-full rounded bg-slate-950 border border-slate-700 p-3 font-mono text-xs leading-relaxed focus:border-emerald-500 outline-none"
            />
            <div className="flex items-center gap-2">
              <button
                onClick={save}
                disabled={!selected || !drafts[selected] || saving}
                title={saving ? "a save is still in flight" : undefined}
                className="rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-40 px-4 py-2 text-sm"
              >
                {saving ? "Saving…" : "Save as new version"}
              </button>
              {policy.dirty && <span className="text-xs text-amber-400">unsaved changes</span>}
            </div>
          </>
        )}

        {state === "AWAITING_APPROVAL" && (
          <div className="rounded-lg border border-purple-800 bg-purple-950/30 p-4 space-y-3">
            <h3 className="text-sm font-semibold text-purple-300">Approval gate — nothing is built here; you get a build prompt after approval</h3>
            {staleMap && (
              <p className="text-xs text-amber-300">
                The specification changed after your last look — the review above has been reloaded.
                Read it and click Approve again to approve the current versions.
              </p>
            )}
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              rows={2}
              placeholder="Comment (required for revisions)…"
              className="w-full rounded bg-slate-950 border border-slate-700 px-3 py-2 text-sm focus:border-purple-500 outline-none"
            />
            <div className="flex gap-2">
              <button
                onClick={() => decide("APPROVED")}
                disabled={!!blocked || !review}
                title={blocked ?? (review ? "approve the reviewed versions" : "loading the review…")}
                className="rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 disabled:hover:bg-emerald-600 px-4 py-2 text-sm font-medium"
              >
                Approve & generate build prompt
              </button>
              <button onClick={() => decide("REVISION_REQUESTED")}
                className="rounded bg-amber-600 hover:bg-amber-500 px-4 py-2 text-sm">
                Request revisions
              </button>
              <button onClick={() => decide("REJECTED")}
                className="rounded border border-red-700 text-red-300 hover:bg-red-900/30 px-4 py-2 text-sm">
                Reject
              </button>
            </div>
            {blocked && (
              <p className="text-xs text-amber-400">
                Approve is disabled: {blocked}.{dirtyCount > 1 ? ` (${dirtyCount} artifacts have unsaved edits)` : ""}
              </p>
            )}
          </div>
        )}
        {error && <p className="text-sm text-red-400">{error}</p>}

        {view === "editor" && history.length > 1 && (
          <details className="text-sm">
            <summary className="cursor-pointer text-slate-400">Version history ({history.length})</summary>
            <ul className="mt-2 space-y-1">
              {history.map((h) => (
                <li key={h.version} className="text-slate-400">
                  <button
                    className="underline decoration-dotted hover:text-slate-200"
                    onClick={() =>
                      selected &&
                      setDrafts((d) => ({
                        ...d,
                        [selected]: restoredFromHistory(d[selected], h.content),
                      }))
                    }
                  >
                    v{h.version}
                  </button>{" "}
                  by {h.created_by} · {new Date(h.created_at).toLocaleString()}
                </li>
              ))}
            </ul>
          </details>
        )}
        {view === "editor" && current && drafts[selected] && (
          <p className="text-xs text-slate-500">
            Viewing {current.slug} (server v{current.current_version}; your draft is based on
            v{drafts[selected].baseVersion}). Edits create new versions; approval freezes the
            versions current at that moment, so later edits never enter the build prompt.
          </p>
        )}
      </div>
    </div>
  );
}

function ReviewPanel({ review }: { review: Review }) {
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-slate-700 bg-slate-900/50 p-4 space-y-4">
        <h3 className="text-sm font-semibold text-slate-200">
          Approval review — exactly what the coding agent will receive
        </h3>
        <Section title="In scope" items={review.in_scope} tone="text-emerald-300" />
        <Section title="Out of scope" items={review.out_of_scope} tone="text-red-300" />
        <Section title="Assumptions" items={review.assumptions} tone="text-slate-300" />
        <Section title="Decisions (yours)" items={review.decisions} tone="text-amber-300" />
        {review.decision_history && review.decision_history.length > 0 && (
          <Section
            title="Prior decisions — a later change request may have revised these"
            items={review.decision_history}
            tone="text-slate-500 line-through decoration-slate-600"
          />
        )}
        {review.interpretations && review.interpretations.length > 0 && (
          <Section
            title="Interpreted answers — verify these at approval"
            items={review.interpretations}
            tone="text-amber-300"
          />
        )}
        {review.unresolved && review.unresolved.length > 0 && (
          <Section title="Still unresolved" items={review.unresolved} tone="text-red-300" />
        )}
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Acceptance criteria</h4>
          <ul className="mt-1 space-y-1 text-sm text-slate-300">
            {review.acceptance_criteria.map((c) => (
              <li key={c.id}>
                <span className="text-slate-500">{c.id}:</span> {c.criterion}
              </li>
            ))}
            {review.acceptance_criteria.length === 0 && <li className="text-slate-500">none recorded</li>}
          </ul>
        </div>
      </div>
      <details className="text-sm">
        <summary className="cursor-pointer text-slate-400">
          Full source documents ({review.sources.length}) — open a tab to read them whole
        </summary>
        <ul className="mt-2 space-y-1">
          {review.sources.map((s) => (
            <li key={s.slug} className="text-slate-400">
              {s.title} · v{s.version} <span className="text-slate-600">({s.slug})</span>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

function Section({ title, items, tone }: { title: string; items: string[] | null; tone: string }) {
  return (
    <div>
      <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-400">{title}</h4>
      {items === null ? (
        // D-030: a missing heading is NOT the same as an empty list — the
        // document simply doesn't say; send the operator to the full source.
        <p className="mt-1 text-sm text-slate-500 italic">
          not stated as a section — check the full source documents below
        </p>
      ) : (
        <ul className="mt-1 space-y-1 text-sm">
          {items.map((x, i) => (
            <li key={i} className={tone}>
              • {x}
            </li>
          ))}
          {items.length === 0 && <li className="text-slate-500">explicitly nothing here</li>}
        </ul>
      )}
    </div>
  );
}
