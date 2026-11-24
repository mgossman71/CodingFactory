import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ProjectDetail } from "../api";
import { StateBadge } from "./Dashboard";
import DiscussionTab from "../components/DiscussionTab";
import FeedbackPanel from "../components/FeedbackPanel";
import SpecsTab from "../components/SpecsTab";
import TasksTab from "../components/TasksTab";
import HandoffTab from "../components/HandoffTab";
import ActivityTab from "../components/ActivityTab";

type Tab = "discussion" | "specs" | "tasks" | "handoff" | "activity";

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [tab, setTab] = useState<Tab>("discussion");
  const [version, setVersion] = useState(0); // bumped on WS events to trigger refetch
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");

  const reload = useCallback(() => {
    if (!id) return;
    api.project(id).then(setProject).catch((e) => setError(String(e)));
  }, [id]);

  useEffect(() => {
    reload();
  }, [reload, version]);

  // Live updates: any event bumps `version`; tabs also subscribe themselves.
  useEffect(() => {
    if (!id) return;
    let ws: WebSocket | null = null;
    let retry: number;
    try {
      ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/projects/${id}`);
      ws.onmessage = () => setVersion((v) => v + 1);
      ws.onclose = () => {
        retry = window.setTimeout(() => setVersion((v) => v + 1), 2000);
      };
    } catch {
      /* dev server without WS proxy: fall back to polling */
    }
    const poll = window.setInterval(() => setVersion((v) => v + 1), 5000);
    return () => {
      ws?.close();
      window.clearTimeout(retry);
      window.clearInterval(poll);
    };
  }, [id]);

  if (!project) return <p className="text-slate-400">{error || "Loading…"}</p>;

  const terminal = ["COMPLETED", "CANCELLED"].includes(project.state);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Link to="/" className="text-sm text-slate-400 hover:text-slate-200">← Projects</Link>
        <h1 className="text-xl font-bold">{project.name}</h1>
        <StateBadge state={project.state} />
        <div className="ml-auto flex gap-2">
          {project.state === "FAILED" && (
            <button onClick={() => api.resume(project.id).then(reload).catch((e) => setActionError(`Resume failed: ${e}`))}
              className="text-sm rounded border border-amber-600 text-amber-300 px-3 py-1 hover:bg-amber-900/30">
              Resume
            </button>
          )}
          {!terminal && (
            <button onClick={() => api.cancel(project.id).then(reload)}
              className="text-sm rounded border border-red-700 text-red-300 px-3 py-1 hover:bg-red-900/30">
              Cancel
            </button>
          )}
          <button
            onClick={() => {
              if (window.confirm("Start this project over? Wipes the discussion, specs, task plan, and activity history, then re-runs discovery from the original idea."))
                api.restart(project.id).then(reload).catch((e) => setActionError(`Start over failed: ${e}`));
            }}
            className="text-sm rounded border border-slate-600 text-slate-300 px-3 py-1 hover:bg-slate-700/40">
            Start over
          </button>
          <button
            onClick={() => {
              if (window.confirm(`Delete project "${project.name}" permanently? Everything it produced is removed.`))
                api.deleteProject(project.id).then(() => navigate("/")).catch((e) => setActionError(`Delete failed: ${e}`));
            }}
            className="text-sm rounded border border-red-800 text-red-400 px-3 py-1 hover:bg-red-950/60">
            Delete
          </button>
        </div>
      </div>

      {actionError && (
        <div className="rounded border border-red-800 bg-red-950/50 p-3 text-sm text-red-300">{actionError}</div>
      )}

      {project.error && (
        <div className="rounded border border-red-800 bg-red-950/50 p-3 text-sm text-red-300">{project.error}</div>
      )}

      <p className="text-sm text-slate-400">{project.idea}</p>

      <FeedbackPanel projectId={project.id} state={project.state} onAction={reload} />

      <nav className="flex gap-1 border-b border-slate-800">
        {(
          ([
            ["discussion", "Discussion"],
            ["specs", `Specs (${project.artifacts.length})`],
            ["tasks", `Tasks (${project.task_count})`],
            ...(project.state === "COMPLETED" ? ([["handoff", "Build prompt"]] as [Tab, string][]) : []),
            ["activity", "Activity"],
          ] as [Tab, string][])
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`px-4 py-2 text-sm rounded-t ${tab === key ? "bg-slate-800 text-white" : "text-slate-400 hover:text-slate-200"}`}
          >
            {label}
          </button>
        ))}
      </nav>

      {tab === "discussion" && (
        <DiscussionTab projectId={project.id} state={project.state} iteration={project.iteration} version={version} />
      )}
      {tab === "specs" && <SpecsTab projectId={project.id} state={project.state} version={version} onAction={reload} />}
      {tab === "tasks" && <TasksTab projectId={project.id} version={version} />}
      {tab === "handoff" && <HandoffTab projectId={project.id} state={project.state} version={version} />}
      {tab === "activity" && <ActivityTab projectId={project.id} version={version} />}
    </div>
  );
}
