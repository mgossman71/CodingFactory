export type ProjectState =
  | "IDEA" | "DISCOVERY" | "AWAITING_INPUT" | "AWAITING_APPROVAL"
  | "PLANNING" | "COMPLETED" | "FAILED" | "CANCELLED";

export interface Project {
  id: string;
  name: string;
  idea: string;
  state: ProjectState;
  discovery_round: number;
  iteration: number; // change-request cycle; scopes the question gate + answer form
  error: string | null;
  created_at: string;
}

export interface ProjectDetail extends Project {
  task_count: number;
  artifacts: string[];
}

export interface QuestionMeta {
  why_it_matters?: string;
  options?: string[];
  recommended_option?: string;
}

export interface Message {
  id: string;
  agent: string;
  kind: string; // message | question | question_candidate | answer | change_request
  content: string;
  round: number;
  seq: number;
  answered: boolean;
  iteration: number;
  meta: QuestionMeta | null; // null on older questions -> plain text (spec: legacy rendering)
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

export interface Task {
  id: string;
  number: number;
  title: string;
  description: string;
  acceptance_criteria: string;
  category: string;
  dependencies: number[];
  iteration: number; // change-request cycle that planned it (0 = original plan)
  supersedes: number[]; // earlier task numbers this task replaces
}

export interface ReviewCriterion {
  id: string;
  criterion: string;
}

export interface ReviewSource {
  slug: string;
  title: string;
  version: number;
  version_id: string; // immutable artifact-version row id; changes on "Start over"
}

/** The compact approval review: sliced from the same versioned artifacts the
 *  coding agent will receive. A section is null when the documents have no
 *  such heading, [] when it exists but lists nothing; decision_history holds
 *  superseded pre-change-request answers (rendered as history, not decisions). */
export interface Review {
  in_scope: string[] | null;
  out_of_scope: string[] | null;
  assumptions: string[] | null;
  decisions: string[] | null;
  decision_history: string[] | null;
  interpretations: string[] | null;
  unresolved: string[] | null;
  acceptance_criteria: ReviewCriterion[];
  sources: ReviewSource[];
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
  restart: (id: string) => req<Project>(`/api/projects/${id}/restart`, { method: "POST" }),
  renameProject: (id: string, name: string) =>
    req<Project>(`/api/projects/${id}`, { method: "PATCH", body: JSON.stringify({ name }) }),
  async deleteProject(id: string) {
    const resp = await fetch(`/api/projects/${id}`, { method: "DELETE" });
    if (!resp.ok) throw new Error(`${resp.status}: ${(await resp.text()).slice(0, 300)}`);
  },
  messages: (id: string) => req<Message[]>(`/api/projects/${id}/messages`),
  answers: (id: string, answers: { question_id?: string; question?: string; answer: string }[]) =>
    req<Project>(`/api/projects/${id}/answers`, { method: "POST", body: JSON.stringify({ answers }) }),
  artifacts: (id: string) => req<Artifact[]>(`/api/projects/${id}/artifacts`),
  artifactHistory: (id: string, slug: string) =>
    req<ArtifactVersion[]>(`/api/projects/${id}/artifacts/${slug}`),
  saveArtifact: (id: string, slug: string, content: string) =>
    req<Artifact>(`/api/projects/${id}/artifacts/${slug}`, { method: "PUT", body: JSON.stringify({ content }) }),
  review: (id: string) => req<Review>(`/api/projects/${id}/review`),
  approval: (
    id: string,
    decision: "APPROVED" | "REJECTED" | "REVISION_REQUESTED",
    comment?: string,
    artifactVersions?: Record<string, number>,
    artifactVersionIds?: Record<string, string>,
  ) =>
    req<Project>(`/api/projects/${id}/approval`, {
      method: "POST",
      body: JSON.stringify({
        decision,
        comment,
        artifact_versions: artifactVersions,
        artifact_version_ids: artifactVersionIds,
      }),
    }),
  tasks: (id: string) => req<Task[]>(`/api/projects/${id}/tasks`),
  handoffText: (id: string) =>
    fetch(`/api/projects/${id}/handoff`).then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status))))),
  handoffUrl: (id: string) => `/api/projects/${id}/handoff`,
  events: (id: string, afterId = 0) => req<ActivityEvent[]>(`/api/projects/${id}/events?after_id=${afterId}`),
  feedback: (id: string, content: string) =>
    req<Project>(`/api/projects/${id}/feedback`, { method: "POST", body: JSON.stringify({ content }) }),
  llmHealth: () => req<{ ok: boolean }>("/api/health/llm"),
};

export const STATE_COLORS: Record<ProjectState, string> = {
  IDEA: "bg-slate-600",
  DISCOVERY: "bg-blue-600",
  AWAITING_INPUT: "bg-amber-600",
  AWAITING_APPROVAL: "bg-purple-600",
  PLANNING: "bg-cyan-600",
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
