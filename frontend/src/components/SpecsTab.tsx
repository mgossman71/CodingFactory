import { useEffect, useState } from "react";
import { api, Artifact, ArtifactVersion, ProjectState } from "../api";
import { DraftPolicy, edited, freshDraft, refetched, restoredFromHistory, saved, selectArtifact } from "../specDraft";

export default function SpecsTab({
  projectId,
  state,
  version,
  onAction,
}: {
  projectId: string;
  state: ProjectState;
  version: number;
  onAction: () => void;
}) {
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [policy, setPolicy] = useState<DraftPolicy>(freshDraft);
  const [history, setHistory] = useState<ArtifactVersion[]>([]);
  const [comment, setComment] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api.artifacts(projectId).then((a) => {
      setArtifacts(a);
      if (!selected && a.length) {
        setSelected(a[0].slug);
        setPolicy(selectArtifact(a[0]));
      } else if (selected) {
        // Polling must never wipe unsaved typing — refetched decides (specDraft.ts).
        const cur = a.find((x) => x.slug === selected);
        setPolicy((p) => refetched(p, cur));
      }
    });
  }, [projectId, version]);

  useEffect(() => {
    if (!selected) return;
    const cur = artifacts.find((a) => a.slug === selected);
    if (cur) setPolicy(selectArtifact(cur));
    api.artifactHistory(projectId, selected).then(setHistory);
  }, [selected]);

  const current = artifacts.find((a) => a.slug === selected);

  async function save() {
    if (!selected) return;
    try {
      const updated = await api.saveArtifact(projectId, selected, policy.draft);
      setPolicy((p) => saved(p, updated.current_version));
      onAction();
    } catch (e) {
      setError(String(e));
    }
  }

  async function decide(decision: "APPROVED" | "REJECTED" | "REVISION_REQUESTED") {
    setError("");
    try {
      await api.approval(projectId, decision, comment || undefined);
      onAction();
    } catch (e) {
      setError(String(e));
    }
  }

  if (!artifacts.length)
    return <p className="text-sm text-slate-500">No specifications yet — they appear after discovery completes.</p>;

  return (
    <div className="grid gap-4 lg:grid-cols-[220px_1fr]">
      <div className="space-y-1">
        {artifacts.map((a) => (
          <button
            key={a.slug}
            onClick={() => setSelected(a.slug)}
            className={`block w-full text-left text-sm rounded px-3 py-2 ${
              selected === a.slug ? "bg-slate-800" : "hover:bg-slate-900 text-slate-300"
            }`}
          >
            {a.title} <span className="text-slate-500">v{a.current_version}</span>
          </button>
        ))}
      </div>

      <div className="space-y-3">
        {policy.serverNewer > 0 && (
          <div className="rounded border border-amber-700 bg-amber-950/40 p-3 text-sm text-amber-300 space-y-2">
            <p>
              {selected} changed on the server (v{policy.serverNewer}) while you have unsaved
              edits based on v{policy.baseVersion}. Your draft is preserved. Saving now creates
              v{policy.serverNewer + 1} from your draft; the server version stays in history.
            </p>
            {current && (
              <button
                onClick={() => setPolicy(selectArtifact(current))}
                className="rounded border border-amber-600 text-amber-200 hover:bg-amber-900/40 px-3 py-1 text-xs"
              >
                Discard my edits, load v{policy.serverNewer}
              </button>
            )}
          </div>
        )}

        <textarea
          value={policy.draft}
          onChange={(e) => setPolicy((p) => edited(p, e.target.value))}
          rows={22}
          className="w-full rounded bg-slate-950 border border-slate-700 p-3 font-mono text-xs leading-relaxed focus:border-emerald-500 outline-none"
        />
        <div className="flex items-center gap-2">
          <button onClick={save} className="rounded bg-slate-700 hover:bg-slate-600 px-4 py-2 text-sm">
            Save as new version
          </button>
          {policy.dirty && <span className="text-xs text-amber-400">unsaved changes</span>}
        </div>

        {state === "AWAITING_APPROVAL" && (
          <div className="rounded-lg border border-purple-800 bg-purple-950/30 p-4 space-y-3">
            <h3 className="text-sm font-semibold text-purple-300">Approval gate — nothing is built here; you get a build prompt after approval</h3>
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              rows={2}
              placeholder="Comment (required for revisions)…"
              className="w-full rounded bg-slate-950 border border-slate-700 px-3 py-2 text-sm focus:border-purple-500 outline-none"
            />
            <div className="flex gap-2">
              <button onClick={() => decide("APPROVED")}
                className="rounded bg-emerald-600 hover:bg-emerald-500 px-4 py-2 text-sm font-medium">
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
          </div>
        )}
        {error && <p className="text-sm text-red-400">{error}</p>}

        {history.length > 1 && (
          <details className="text-sm">
            <summary className="cursor-pointer text-slate-400">Version history ({history.length})</summary>
            <ul className="mt-2 space-y-1">
              {history.map((h) => (
                <li key={h.version} className="text-slate-400">
                  <button
                    className="underline decoration-dotted hover:text-slate-200"
                    onClick={() => setPolicy((p) => restoredFromHistory(p, h.content))}
                  >
                    v{h.version}
                  </button>{" "}
                  by {h.created_by} · {new Date(h.created_at).toLocaleString()}
                </li>
              ))}
            </ul>
          </details>
        )}
        {current && (
          <p className="text-xs text-slate-500">
            Viewing {current.slug} (server v{current.current_version}; your draft is based on
            v{policy.baseVersion}). Edits create new versions; approval freezes the versions
            current at that moment, so later edits never enter the build prompt.
          </p>
        )}
      </div>
    </div>
  );
}
