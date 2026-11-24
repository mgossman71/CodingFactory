import { useEffect, useState } from "react";
import { api, Task } from "../api";

export default function TasksTab({ projectId, version }: { projectId: string; version: number }) {
  const [tasks, setTasks] = useState<Task[]>([]);

  useEffect(() => {
    api.tasks(projectId).then(setTasks);
  }, [projectId, version]);

  // D-024: in a change-request cycle the plan carries deltas — show which work
  // is new, already delivered, or superseded (replaced by a newer task).
  const supersededBy = new Map<number, number[]>();
  for (const t of tasks)
    for (const old of t.supersedes) supersededBy.set(old, [...(supersededBy.get(old) ?? []), t.number]);
  const maxIteration = tasks.reduce((m, t) => Math.max(m, t.iteration), 0);
  const isDelta = maxIteration > 0;

  if (!tasks.length)
    return (
      <p className="text-sm text-slate-500">
        No tasks yet. The task plan appears after the specification is approved (Dev Manager planning phase), and
        is then embedded in the build handoff prompt.
      </p>
    );

  return (
    <div className="space-y-2">
      {tasks.map((t) => (
        <div key={t.id} className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
          <div className="flex items-center gap-3 text-sm">
            <span className="font-mono text-slate-500">#{t.number}</span>
            <span className="font-medium">{t.title}</span>
            <span className="rounded bg-slate-800 px-1.5 py-0.5 text-xs text-slate-400">{t.category}</span>
            {isDelta && (
              <span
                className={`rounded px-1.5 py-0.5 text-xs ${
                  supersededBy.has(t.number)
                    ? "border border-red-800 bg-red-950/60 text-red-300"
                    : t.iteration === maxIteration
                      ? "border border-emerald-700 bg-emerald-900/50 text-emerald-300"
                      : "bg-slate-800 text-slate-500"
                }`}
              >
                {supersededBy.has(t.number)
                  ? `superseded by ${supersededBy.get(t.number)!.map((n) => `#${n}`).join(", ")}`
                  : t.iteration === maxIteration
                    ? "new in this change request"
                    : "delivered"}
              </span>
            )}
            {t.dependencies.length > 0 && (
              <span className="ml-auto text-xs text-slate-500">
                after {t.dependencies.map((d) => `#${d}`).join(", ")}
              </span>
            )}
          </div>
          {(t.description || t.acceptance_criteria) && (
            <details className="mt-2 text-sm text-slate-400">
              <summary className="cursor-pointer">Description & acceptance criteria</summary>
              {t.description && <pre className="mt-1 whitespace-pre-wrap font-sans">{t.description}</pre>}
              {t.acceptance_criteria && (
                <>
                  <p className="mt-2 font-medium text-slate-300">Acceptance criteria:</p>
                  <pre className="mt-1 whitespace-pre-wrap font-sans">{t.acceptance_criteria}</pre>
                </>
              )}
            </details>
          )}
        </div>
      ))}
    </div>
  );
}
