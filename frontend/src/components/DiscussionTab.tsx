import { useEffect, useState } from "react";
import { AGENT_STYLES, api, Message, ProjectState } from "../api";
import { Selection, buildAnswerPayload, isRecommended, pendingQuestions } from "../questionForm";

export default function DiscussionTab({
  projectId,
  state,
  iteration,
  version,
}: {
  projectId: string;
  state: ProjectState;
  iteration: number;
  version: number;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [selections, setSelections] = useState<Record<string, Selection>>({});
  const [freeform, setFreeform] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api.messages(projectId).then(setMessages).catch((e) => setError(String(e)));
  }, [projectId, version]);

  // Current iteration only, ranked (questionForm.ts). Candidates/answered/
  // stale-iteration questions never appear in the form.
  const questions = pendingQuestions(messages, iteration);
  const canAnswer = state === "AWAITING_INPUT";

  function select(questionId: string, sel: Selection) {
    setSelections((prev) => ({ ...prev, [questionId]: sel }));
  }

  async function submit() {
    setError("");
    const payload = buildAnswerPayload(questions, selections, freeform);
    if (!payload.length) return;
    try {
      await api.answers(projectId, payload);
      setSelections({});
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
              {m.kind === "question_candidate" && (
                <span className="text-slate-500 font-medium">candidate question (internal — the Architect consolidates)</span>
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

      {canAnswer && questions.length > 0 && (
        <div className="rounded-lg border border-amber-800 bg-amber-950/30 p-4 space-y-4">
          <h3 className="text-sm font-semibold text-amber-300">
            The team needs your input — {questions.length} question{questions.length > 1 ? "s" : ""} that
            change what gets built
          </h3>
          <p className="text-xs text-slate-400">
            Answer any subset now; the team waits until everything here is answered. You can type your own
            answer for every question.
          </p>
          {questions.map((q, i) => {
            const meta = q.meta; // null on older questions -> plain text + free answer only
            const sel = selections[q.id] ?? {};
            const options = meta?.options ?? [];
            return (
              <div key={q.id} className="space-y-1">
                <p className="text-sm">
                  <span className="mr-2 rounded bg-amber-900/60 px-1.5 py-0.5 text-xs text-amber-300">
                    Q{i + 1}/{questions.length}
                  </span>
                  {q.content}
                </p>
                {meta?.why_it_matters && (
                  <p className="text-xs text-slate-400">Why it matters: {meta.why_it_matters}</p>
                )}
                {options.map((opt) => (
                  <label
                    key={opt}
                    className={`flex items-center gap-2 rounded border px-3 py-2 text-sm cursor-pointer ${
                      sel.option === opt && !sel.custom?.trim()
                        ? "border-amber-500 bg-amber-900/30"
                        : "border-slate-700 hover:border-slate-500"
                    }`}
                  >
                    <input
                      type="radio"
                      name={`question-${q.id}`}
                      className="accent-amber-500"
                      checked={sel.option === opt && !sel.custom?.trim()}
                      onChange={() => select(q.id, { ...sel, option: opt })}
                    />
                    <span>{opt}</span>
                    {isRecommended(q, opt) && (
                      <span className="rounded bg-emerald-900/60 px-1.5 py-0.5 text-xs text-emerald-300">
                        recommended
                      </span>
                    )}
                  </label>
                ))}
                <input
                  value={sel.custom ?? ""}
                  onChange={(e) => select(q.id, { ...sel, custom: e.target.value })}
                  className="w-full rounded bg-slate-950 border border-slate-700 px-3 py-2 text-sm focus:border-amber-500 outline-none"
                  placeholder={options.length ? "Or write your own answer…" : "Your answer…"}
                />
              </div>
            );
          })}
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
