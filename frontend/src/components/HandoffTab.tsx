import { useEffect, useState } from "react";
import { api, ProjectState } from "../api";
import { copyText } from "../clipboard";

/**
 * The final deliverable: a single build prompt the operator copies or downloads
 * and hands to a more capable coding agent to do the build + test phases.
 */
export default function HandoffTab({
  projectId,
  state,
  version,
}: {
  projectId: string;
  state: ProjectState;
  version: number;
}) {
  const [prompt, setPrompt] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState("");

  useEffect(() => {
    if (state !== "COMPLETED") return;
    setError("");
    api
      .handoffText(projectId)
      .then(setPrompt)
      .catch((e) => {
        setPrompt(null);
        setError(String(e));
      });
  }, [projectId, state, version]);

  if (state !== "COMPLETED")
    return (
      <p className="text-sm text-slate-500">
        The build prompt is generated once the specification is approved and the task plan is ready.
      </p>
    );

  if (error) return <p className="text-sm text-red-400">Could not load the handoff prompt: {error}</p>;
  if (prompt === null) return <p className="text-sm text-slate-500">Generating the build prompt…</p>;

  async function copy() {
    // copyText() has a fallback for insecure origins (http://<host>:8000),
    // where browsers disable navigator.clipboard entirely.
    if (await copyText(prompt ?? "")) {
      setCopied(true);
      setCopyError("");
      window.setTimeout(() => setCopied(false), 1500);
    } else {
      setCopyError("clipboard blocked by the browser over plain HTTP — use Download .md instead");
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-emerald-300">Build handoff prompt</h3>
        <span className="text-xs text-slate-400">
          Copy this into a coding agent (or download it) to run the build & test phases.
        </span>
        <div className="ml-auto flex gap-2">
          <button
            onClick={copy}
            className="text-sm rounded bg-emerald-600 hover:bg-emerald-500 px-3 py-1 font-medium"
          >
            {copied ? "Copied" : "Copy to clipboard"}
          </button>
          <a
            href={api.handoffUrl(projectId)}
            className="text-sm rounded border border-slate-700 text-slate-300 hover:bg-slate-800 px-3 py-1"
          >
            Download .md
          </a>
        </div>
      </div>
      <pre className="whitespace-pre-wrap break-words rounded bg-slate-950 border border-slate-800 p-4 font-mono text-xs leading-relaxed max-h-[70vh] overflow-y-auto">
        {prompt}
      </pre>
      {copyError && <p className="text-sm text-red-400">{copyError}</p>}
    </div>
  );
}
