export type ProjectState =
  | "IDEA" | "DISCOVERY" | "AWAITING_INPUT" | "AWAITING_APPROVAL"
  | "PLANNING" | "BUILDING" | "COMPLETED" | "FAILED" | "CANCELLED";

export interface Project {
  id: string;
  name: string;
  idea: string;
  state: ProjectState;
  discovery_round: number;
  error: string | null;
  created_at: string;
}

export interface ProjectDetail extends Project {
  task_counts: Record<string, number>;
  artifacts: string[];
}

export interface Message {
  id: string;
  agent: string;
  kind: string;
  content: string;
  round: number;
  seq: number;
  answered: boolean;
  created_at: string;
}

export interface Artifact {
  slug: string;
  title: string;
  current_version: number;
  content: string;
  updated_at: string;
}

export interface ArtifactVersion {
  version: number;
  created_by: string;
  created_at: string;
  content: string;
}

export interface TaskAttempt {
  id: string;
  attempt_number: number;
  status: string;
  exit_code: number | null;
  result_summary: string;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
}

export interface Task {
  id: string;
  number: number;
  title: string;
  description: string;
  acceptance_criteria: string;
  status: string;
  attempts: number;
  max_attempts: number;
  dependencies: number[];
  attempts_log: TaskAttempt[];
}

export interface LogEntry {
  id: string;
  kind: string;
  content: string;
  created_at: string;
}

export interface ActivityEvent {
  id: number;
  type: string;
  payload: string;
  created_at: string;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`${resp.status}: ${text.slice(0, 300)}`);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  projects: () => req<Project[]>("/api/projects"),
  project: (id: string) => req<ProjectDetail>(`/api/projects/${id}`),
  createProject: (idea: string, name?: string) =>
    req<Project>("/api/projects", { method: "POST", body: JSON.stringify({ idea, name }) }),
  cancel: (id: string) => req<Project>(`/api/projects/${id}/cancel`, { method: "POST" }),
  resume: (id: string) => req<Project>(`/api/projects/${id}/resume`, { method: "POST" }),
  messages: (id: string) => req<Message[]>(`/api/projects/${id}/messages`),
  answers: (id: string, answers: { question_id?: string; question: string; answer: string }[]) =>
    req<Project>(`/api/projects/${id}/answers`, { method: "POST", body: JSON.stringify({ answers }) }),
  artifacts: (id: string) => req<Artifact[]>(`/api/projects/${id}/artifacts`),
  artifactHistory: (id: string, slug: string) =>
    req<ArtifactVersion[]>(`/api/projects/${id}/artifacts/${slug}`),
  saveArtifact: (id: string, slug: string, content: string) =>
    req<Artifact>(`/api/projects/${id}/artifacts/${slug}`, { method: "PUT", body: JSON.stringify({ content }) }),
  approval: (id: string, decision: "APPROVED" | "REJECTED" | "REVISION_REQUESTED", comment?: string) =>
    req<Project>(`/api/projects/${id}/approval`, { method: "POST", body: JSON.stringify({ decision, comment }) }),
  tasks: (id: string) => req<Task[]>(`/api/projects/${id}/tasks`),
  logs: (id: string, attemptId: string) => req<LogEntry[]>(`/api/projects/${id}/attempts/${attemptId}/logs`),
  events: (id: string, afterId = 0) => req<ActivityEvent[]>(`/api/projects/${id}/events?after_id=${afterId}`),
  llmHealth: () => req<{ ok: boolean }>("/api/health/llm"),
};

export const STATE_COLORS: Record<ProjectState, string> = {
  IDEA: "bg-slate-600",
  DISCOVERY: "bg-blue-600",
  AWAITING_INPUT: "bg-amber-600",
  AWAITING_APPROVAL: "bg-purple-600",
  PLANNING: "bg-cyan-600",
  BUILDING: "bg-emerald-600",
  COMPLETED: "bg-green-700",
  FAILED: "bg-red-700",
  CANCELLED: "bg-slate-700",
};

export const AGENT_STYLES: Record<string, { label: string; color: string }> = {
  "product-manager": { label: "Product Manager", color: "text-blue-400 border-blue-700/50 bg-blue-950/40" },
  architect: { label: "Architect", color: "text-purple-400 border-purple-700/50 bg-purple-950/40" },
  "dev-manager": { label: "Dev Manager", color: "text-cyan-400 border-cyan-700/50 bg-cyan-950/40" },
  "release-manager": { label: "Release Manager", color: "text-green-400 border-green-700/50 bg-green-950/40" },
  developer: { label: "Developer", color: "text-emerald-400 border-emerald-700/50 bg-emerald-950/40" },
  user: { label: "You", color: "text-amber-300 border-amber-700/50 bg-amber-950/40" },
  system: { label: "System", color: "text-slate-400 border-slate-700/50 bg-slate-900/40" },
};
