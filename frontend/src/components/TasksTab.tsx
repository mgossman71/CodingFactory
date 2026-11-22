import { useEffect, useState } from "react";
import { api, LogEntry, Task, TaskAttempt } from "../api";

const STATUS_STYLE: Record<string, string> = {
  BACKLOG: "bg-slate-700",
  READY: "bg-sky-700",
  IN_PROGRESS: "bg-emerald-600 animate-pulse",
  TESTING: "bg-cyan-700",
  REVIEW: "bg-purple-700",
  BLOCKED: "bg-amber-700",
  COMPLETED: "bg-green-800",
  FAILED: "bg-red-800",
};

export default function TasksTab({ projectId, version }: { projectId: string; version: number }) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [openAttempt, setOpenAttempt] = useState<string | null>(null);

  useEffect(() => {
    api.tasks(projectId).then(setTasks);
  }, [projectId, version]);

  if (!tasks.length)
    return (
      <p className="text-sm text-slate-500">
        No tasks yet. Tasks appear after the specification is approved (Dev Manager planning phase).
      </p>
    );

  return (
    <div className="space-y-2">
      {tasks.map((t) => (
        <div key={t.id} className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
          <div className="flex items-center gap-3 text-sm">
            <span className="font-mono text-slate-500">#{t.number}</span>
            <span className="font-medium">{t.title}</span>
            <span className={`text-[11px] px-2 py-0.5 rounded ${STATUS_STYLE[t.status] || "bg-slate-700"}`}>
              {t.status.replaceAll("_", " ")}
            </span>
            <span className="ml-auto text-xs text-slate-500">
              {t.attempts}/{t.max_attempts} attempts
              {t.dependencies.length > 0 && <> · after {t.dependencies.map((d) => `#${d}`).join(", ")}</>}
            </span>
          </div>
          {t.description && (
            <details className="mt-2 text-sm text-slate-400">
              <summary className="cursor-pointer">Description & criteria</summary>
              <pre className="mt-1 whitespace-pre-wrap font-sans">{t.description}</pre>
            </details>
          )}
          {t.attempts_log.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-2">
              {t.attempts_log.map((a) => (
                <AttemptChip key={a.id} projectId={projectId} attempt={a} t={t}
                  open={openAttempt === a.id}
                  onToggle={() => setOpenAttempt(openAttempt === a.id ? null : a.id)} />
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function AttemptChip({
  projectId,
  attempt,
  t,
  open,
  onToggle,
}: {
  projectId: string;
  attempt: TaskAttempt;
  t: Task;
  open: boolean;
  onToggle: () => void;
}) {
  const [logs, setLogs] = useState<LogEntry[] | null>(null);
  useEffect(() => {
    if (open && logs === null) api.logs(projectId, attempt.id).then(setLogs);
  }, [open]);

  const color = attempt.status === "PASSED" ? "text-green-400 border-green-800" :
    attempt.status === "RUNNING" ? "text-emerald-300 border-emerald-700" : "text-red-400 border-red-800";

  return (
    <div className="text-xs">
      <button onClick={onToggle} className={`rounded border px-2 py-1 ${color}`}>
        attempt {attempt.attempt_number}: {attempt.status.toLowerCase()}
        {attempt.duration_ms ? ` (${(attempt.duration_ms / 1000).toFixed(0)}s)` : ""}
      </button>
      {open && (
        <div className="mt-2 rounded bg-slate-950 border border-slate-800 p-3 max-h-96 overflow-y-auto">
          <p className="mb-2 text-slate-300">{attempt.result_summary}</p>
          {(logs ?? []).map((l) => (
            <div key={l.id} className="mb-2">
              <span className="text-slate-500">[{l.kind}]</span>
              <pre className="mt-1 whitespace-pre-wrap font-mono text-[11px] text-slate-300">{l.content.slice(0, 4000)}</pre>
            </div>
          ))}
          {logs === null && <p className="text-slate-500">loading logs…</p>}
        </div>
      )}
    </div>
  );
}
