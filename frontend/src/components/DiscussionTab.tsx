import { useEffect, useState } from "react";
import { AGENT_STYLES, api, Message, ProjectState } from "../api";

export default function DiscussionTab({
  projectId,
  state,
  version,
}: {
  projectId: string;
  state: ProjectState;
  version: number;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [freeform, setFreeform] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api.messages(projectId).then(setMessages).catch((e) => setError(String(e)));
  }, [projectId, version]);

  const questions = messages.filter((m) => m.kind === "question" && !m.answered);
  const canAnswer = state === "AWAITING_INPUT";

  async function submit() {
    setError("");
    const payload: { question_id?: string; question: string; answer: string }[] = questions
      .filter((q) => (answers[q.id] || "").trim())
      .map((q) => ({ question_id: q.id, question: q.content, answer: answers[q.id].trim() }));
    if (freeform.trim()) payload.push({ question: "", answer: freeform.trim() });
    if (!payload.length) return;
    try {
      await api.answers(projectId, payload);
      setAnswers({});
      setFreeform("");
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="space-y-3">
      {messages.map((m) => {
        const style = AGENT_STYLES[m.agent] ?? AGENT_STYLES.system;
        return (
          <div key={m.id} className={`rounded-lg border p-4 ${style.color.split(" ").slice(1).join(" ")}`}>
            <div className="flex items-baseline gap-3 text-xs">
              <span className={`font-semibold ${style.color.split(" ")[0]}`}>{style.label}</span>
              {m.kind === "question" && (
                <span className="text-amber-400 font-medium">? question for you</span>
              )}
              {m.kind === "change_request" && (
                <span className="text-purple-400 font-medium">change request</span>
              )}
              {m.kind === "answer" && <span className="text-slate-400">answered</span>}
              <span className="ml-auto text-slate-500">{new Date(m.created_at).toLocaleTimeString()}</span>
            </div>
            <pre className="mt-2 whitespace-pre-wrap text-sm font-sans leading-relaxed">{m.content}</pre>
          </div>
        );
      })}
      {messages.length === 0 && (
        <p className="text-sm text-slate-500">
          {state === "DISCOVERY" ? "The Product Manager is analyzing your idea…" : "No messages yet."}
        </p>
      )}

      {canAnswer && (
        <div className="rounded-lg border border-amber-800 bg-amber-950/30 p-4 space-y-3">
          <h3 className="text-sm font-semibold text-amber-300">The team needs your input</h3>
          {questions.map((q) => (
            <div key={q.id}>
              <p className="text-sm mb-1">{q.content}</p>
              <input
                value={answers[q.id] || ""}
                onChange={(e) => setAnswers({ ...answers, [q.id]: e.target.value })}
                className="w-full rounded bg-slate-950 border border-slate-700 px-3 py-2 text-sm focus:border-amber-500 outline-none"
                placeholder="Your answer…"
              />
            </div>
          ))}
          <div>
            <p className="text-sm mb-1 text-slate-400">Additional requirements (optional)</p>
            <textarea
              value={freeform}
              onChange={(e) => setFreeform(e.target.value)}
              rows={2}
              className="w-full rounded bg-slate-950 border border-slate-700 px-3 py-2 text-sm focus:border-amber-500 outline-none"
            />
          </div>
          <button onClick={submit} className="rounded bg-amber-600 hover:bg-amber-500 px-4 py-2 text-sm font-medium">
            Send answers
          </button>
        </div>
      )}
      {error && <p className="text-sm text-red-400">{error}</p>}
    </div>
  );
}
