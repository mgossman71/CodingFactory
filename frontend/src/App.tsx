import { Route, Routes, Link } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import ProjectPage from "./pages/ProjectPage";

export default function App() {
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-800 bg-slate-900/60">
        <div className="mx-auto max-w-6xl px-4 py-3 flex items-center gap-6">
          <Link to="/" className="font-bold text-lg tracking-tight">
            Coding<span className="text-emerald-400">Factory</span>
          </Link>
          <span className="text-xs text-slate-500">idea → spec → build-ready prompt</span>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/projects/:id" element={<ProjectPage />} />
        </Routes>
      </main>
    </div>
  );
}
