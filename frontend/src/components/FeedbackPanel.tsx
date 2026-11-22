import { useEffect, useState } from "react";
import { api, ProjectState, UserFeedbackItem } from "../api";

/**
 * User input after the approval gate:
 * - COMPLETED: submit a change request (delta discovery -> approval -> new tasks).
 * - BUILDING/PLANNING: queue a NOTE (injected into the next task) or a REPLAN
 *   (pauses the build at the next attempt boundary for a change-request cycle).
 */
export default function FeedbackPanel({
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
  const [content, setContent] = useState("");
  const [mode, setMode] = useState<"NOTE" | "REPLAN">("NOTE");
  const [pending, setPending] = useState<UserFeedbackItem[]>([]);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);

  const building = state === "BUILDING" || state === "PLANNING";
  const completed = state === "COMPLETED";

  useEffect(() => {
    if (building) api.feedbackList(projectId).then(setPending).catch(() => {});
  }, [projectId, building, version]);

  useEffect(() => {
    if (building) setMode("NOTE");
    else setMode("REPLAN");
  }, [building]);

  async function submit() {
    if (!content.trim() || sending) return;
    setError("");
    setSending(true);
    try {
      await api.feedback(projectId, content.trim(), building ? mode : "REPLAN");
      setContent("");
      onAction();
    } catch (e) {
      setError(String(e));
    } finally {
      setSending(false);
    }
  }

  if (!building && !completed) return null;

  return (
    <div className="rounded-lg border border-purple-800 bg-purple-950/30 p-4 space-y-3">
      <h3 className="text-sm font-semibold text-purple-300">
        {completed ? "Request changes to the delivered product" : "Give input while the build runs"}
      </h3>
      <p className="text-xs text-slate-400">
        {completed
          ? "Your feedback restarts discovery for a change-request cycle: revised specs, your approval, then new tasks built on top of the existing code."
          : "A note is added to the next task the builders start. A replan pauses the build after the current task, re-runs discovery with your feedback, and asks your approval before continuing."}
      </p>
      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        rows={3}
        placeholder="e.g. Add a filter by budget on the builds page, and make the homepage darker…"
        className="w-full rounded bg-slate-950 border border-slate-700 px-3 py-2 text-sm focus:border-purple-500 outline-none"
      />
      {building && (
        <div className="flex gap-2">
          {(
            [
              ["NOTE", "Note for next task"],
              ["REPLAN", "Change course (re-plan)"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              onClick={() => setMode(value)}
              className={`text-sm rounded border px-3 py-1 ${
                mode === value
                  ? "border-purple-500 bg-purple-900/50 text-purple-200"
                  : "border-slate-700 text-slate-400 hover:text-slate-200"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      )}
      <div className="flex items-center gap-3">
        <button
          onClick={submit}
          disabled={!content.trim() || sending}
          className="rounded bg-purple-600 hover:bg-purple-500 disabled:opacity-50 px-4 py-2 text-sm font-medium"
        >
          {completed ? "Start change request" : building && mode === "REPLAN" ? "Pause & re-plan" : "Send feedback"}
        </button>
        {error && <span className="text-sm text-red-400">{error}</span>}
      </div>
      {pending.length > 0 && (
        <div className="space-y-1 pt-1 border-t border-purple-900/60">
          {pending.map((f) => (
            <div key={f.id} className="flex items-baseline gap-2 text-xs">
              <span className={f.mode === "REPLAN" ? "text-rose-300" : "text-sky-300"}>
                {f.mode === "REPLAN" ? "re-plan" : "note"}
              </span>
              <span className="text-slate-300 truncate">{f.content}</span>
              <span className="ml-auto text-slate-500">{f.consumed_at ? "applied" : "queued"}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
