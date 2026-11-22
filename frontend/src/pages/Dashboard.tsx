import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, Project, ProjectState, STATE_COLORS } from "../api";

export default function Dashboard() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [idea, setIdea] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const timer = useRef<number | undefined>(undefined);

  const load = useCallback(() => {
    api.projects().then(setProjects).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    load();
    timer.current = window.setInterval(load, 4000);
    return () => window.clearInterval(timer.current);
  }, [load]);

  async function create() {
    if (idea.trim().length < 10) return;
    setBusy(true);
    setError("");
    try {
      const p = await api.createProject(idea.trim());
      navigate(`/projects/${p.id}`);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const active = projects.filter((p) => !["COMPLETED", "FAILED", "CANCELLED"].includes(p.state));
  const done = projects.filter((p) => ["COMPLETED", "FAILED", "CANCELLED"].includes(p.state));

  return (
    <div className="space-y-8">
      <section className="rounded-lg border border-slate-800 bg-slate-900/50 p-5">
        <h2 className="font-semibold mb-2">New project</h2>
        <textarea
          value={idea}
          onChange={(e) => setIdea(e.target.value)}
          rows={3}
          placeholder="Describe your software idea in plain English… e.g. Build a vehicle maintenance application that tracks maintenance schedules for cars and lawn equipment."
          className="w-full rounded bg-slate-950 border border-slate-700 p-3 text-sm focus:border-emerald-500 outline-none"
        />
        <div className="mt-3 flex items-center gap-3">
          <button
            onClick={create}
            disabled={busy || idea.trim().length < 10}
            className="rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 px-4 py-2 text-sm font-medium"
          >
            {busy ? "Starting discovery…" : "Start discovery"}
          </button>
          {error && <span className="text-sm text-red-400">{error}</span>}
        </div>
      </section>

      <ProjectList title="Active projects" projects={active} />
      {done.length > 0 && <ProjectList title="Finished" projects={done} />}
    </div>
  );
}

function ProjectList({ title, projects }: { title: string; projects: Project[] }) {
  return (
    <section>
      <h2 className="text-sm uppercase tracking-wide text-slate-400 mb-3">{title}</h2>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {projects.map((p) => (
          <Link
            key={p.id}
            to={`/projects/${p.id}`}
            className="block rounded-lg border border-slate-800 bg-slate-900/50 hover:border-slate-600 p-4"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium truncate">{p.name}</span>
              <StateBadge state={p.state} />
            </div>
            <p className="mt-2 text-sm text-slate-400 line-clamp-2">{p.idea}</p>
          </Link>
        ))}
        {projects.length === 0 && <p className="text-sm text-slate-500 col-span-full">None yet.</p>}
      </div>
    </section>
  );
}

export function StateBadge({ state }: { state: ProjectState }) {
  return (
    <span className={`text-[11px] px-2 py-0.5 rounded whitespace-nowrap ${STATE_COLORS[state]}`}>
      {state.replaceAll("_", " ")}
    </span>
  );
}
