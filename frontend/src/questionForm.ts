/**
 * Question-form policy (review feature, D-028 frontend half).
 *
 * The Architect's consolidated batch (kind "question") is the only thing the
 * operator answers — PM candidates (kind "question_candidate") are discussion.
 * These pure helpers define the form contract:
 *
 *  - pending questions are scoped to the CURRENT iteration (older/stale ones
 *    never appear in the answer form) and keep the ranked order they were
 *    persisted in;
 *  - legacy question rows (no `iteration`/`meta` fields) render as plain
 *    text and remain answerable;
 *  - every option starts unselected; a custom answer is always available
 *    and overrides a selected option;
 *  - submissions may be partial — only answered questions are sent, and only
 *    question ids from the pending list (never stale ones).
 */

export interface QuestionLike {
  id: string;
  kind: string;
  answered: boolean;
  seq?: number; // persisted order == rank; absent on hand-built payloads
  iteration?: number; // absent on payloads predating the field -> treat as current
  content: string;
  meta?: {
    why_it_matters?: string;
    options?: string[];
    recommended_option?: string;
  } | null;
}

/** Ranked (persisted order) unanswered operator-facing questions for `iteration`. */
export function pendingQuestions<T extends QuestionLike>(messages: T[], iteration: number): T[] {
  return messages
    .filter((m) => m.kind === "question" && !m.answered)
    .filter((m) => m.iteration === undefined || m.iteration === iteration)
    .sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
}

export interface Selection {
  option?: string; // chosen option text
  custom?: string; // free-text answer (overrides option)
}

/** What gets sent as this question's answer; "" means unanswered (partial ok). */
export function answerText(sel: Selection | undefined): string {
  if (!sel) return "";
  const custom = (sel.custom ?? "").trim();
  if (custom) return custom;
  return (sel.option ?? "").trim();
}

/** A recommendation badge only when it matches one of the offered options. */
export function isRecommended(q: QuestionLike, option: string): boolean {
  const rec = q.meta?.recommended_option?.trim();
  return !!rec && rec.toLowerCase() === option.trim().toLowerCase();
}

export interface AnswerPayload {
  question_id?: string;
  question?: string;
  answer: string;
}

/**
 * Build the /answers submission: answered questions only (partial allowed),
 * question ids taken from the pending list only, plus an optional free-form
 * note that answers nothing.
 */
export function buildAnswerPayload(
  pending: QuestionLike[],
  selections: Record<string, Selection>,
  freeform: string,
): AnswerPayload[] {
  const payload: AnswerPayload[] = pending
    .map((q) => ({ question_id: q.id, answer: answerText(selections[q.id]) }))
    .filter((a) => a.answer);
  const note = freeform.trim();
  if (note) payload.push({ answer: note });
  return payload;
}
