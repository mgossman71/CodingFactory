import { useState } from "react";
import { api, ProjectState } from "../api";

/**
 * Change request on a delivered project. Submitting restarts discovery for a
 * change-request cycle: revised specs, your approval, then a regenerated
 * build handoff prompt.
 */
export default function FeedbackPanel({
  projectId,
  state,
  onAction,
}: {
  projectId: string;
  state: ProjectState;
  onAction: () => void;
}) {
  const [content, setContent] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);

  if (state !== "COMPLETED") return null;

  async function submit() {
    if (!content.trim() || sending) return;
    setError("");
    setSending(true);
    try {
      await api.feedback(projectId, content.trim());
      setContent("");
      onAction();
    } catch (e) {
      setError(String(e));
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="rounded-lg border border-purple-800 bg-purple-950/30 p-4 space-y-3">
      <h3 className="text-sm font-semibold text-purple-300">Request changes to the specification</h3>
      <p className="text-xs text-slate-400">
        Your feedback restarts discovery for a change-request cycle: revised specs, your approval, then a fresh
        build handoff prompt.
      </p>
      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        rows={3}
        placeholder="e.g. Add a filter by budget on the builds page, and make the homepage darker…"
        className="w-full rounded bg-slate-950 border border-slate-700 px-3 py-2 text-sm focus:border-purple-500 outline-none"
      />
      <div className="flex items-center gap-3">
        <button
          onClick={submit}
          disabled={!content.trim() || sending}
          className="rounded bg-purple-600 hover:bg-purple-500 disabled:opacity-50 px-4 py-2 text-sm font-medium"
        >
          Start change request
        </button>
        {error && <span className="text-sm text-red-400">{error}</span>}
      </div>
    </div>
  );
}
