import { describe, expect, it } from "vitest";
import {
  QuestionLike,
  answerText,
  buildAnswerPayload,
  isRecommended,
  pendingQuestions,
} from "../src/questionForm";

const q = (over: Partial<QuestionLike> & { id: string }): QuestionLike => ({
  kind: "question",
  answered: false,
  iteration: 0,
  content: `question ${over.id}?`,
  meta: null,
  ...over,
});

describe("pendingQuestions", () => {
  it("keeps only unanswered operator-facing questions, ranked by seq", () => {
    const messages = [
      q({ id: "c1", kind: "question_candidate" }),
      q({ id: "a2", seq: 2 }),
      q({ id: "a1", seq: 1 }),
      q({ id: "done", answered: true }),
    ];
    expect(pendingQuestions(messages, 0).map((m) => m.id)).toEqual(["a1", "a2"]);
  });

  it("excludes questions from other iterations (stale / change-request batches)", () => {
    const messages = [q({ id: "old", iteration: 0 }), q({ id: "cur", iteration: 1 })];
    expect(pendingQuestions(messages, 1).map((m) => m.id)).toEqual(["cur"]);
  });

  it("legacy rows without iteration/meta stay visible and answerable", () => {
    const legacy = { id: "l1", kind: "question", answered: false, content: "plain question" };
    const out = pendingQuestions([legacy], 3);
    expect(out.map((m) => m.id)).toEqual(["l1"]);
    expect(out[0].meta).toBeUndefined();
  });
});

describe("answerText", () => {
  it("starts unselected — no answer until the operator chooses", () => {
    expect(answerText(undefined)).toBe("");
    expect(answerText({})).toBe("");
  });

  it("custom free text overrides a selected option", () => {
    expect(answerText({ option: "In-app only", custom: "  in-app, plus SMS  " })).toBe("in-app, plus SMS");
  });

  it("falls back to the selected option", () => {
    expect(answerText({ option: " In-app only " })).toBe("In-app only");
  });
});

describe("isRecommended", () => {
  it("badges only an option that matches the recommendation", () => {
    const m = q({ id: "x", meta: { options: ["a", "b"], recommended_option: "B" } });
    expect(isRecommended(m, "b")).toBe(true);
    expect(isRecommended(m, "a")).toBe(false);
    expect(isRecommended(q({ id: "y" }), "a")).toBe(false); // no meta -> no badge
  });

  it("ignores a recommendation that is not one of the options", () => {
    const m = q({ id: "x", meta: { options: ["a"], recommended_option: "c" } });
    expect(isRecommended(m, "a")).toBe(false);
  });
});

describe("buildAnswerPayload", () => {
  const pending = [q({ id: "q1" }), q({ id: "q2" })];

  it("sends only answered questions (partial submissions allowed)", () => {
    expect(buildAnswerPayload(pending, { q1: { option: "In-app only" } }, "")).toEqual([
      { question_id: "q1", answer: "In-app only" },
    ]);
  });

  it("uses only ids from the pending list — never stale ones", () => {
    const payload = buildAnswerPayload(pending, { q1: { option: "x" }, ghost: { option: "y" } }, "");
    expect(payload.map((a) => a.question_id)).toEqual(["q1"]);
  });

  it("appends the free-form note as an unlinked answer", () => {
    const payload = buildAnswerPayload(pending, { q1: { custom: "yes" } }, "export to CSV");
    expect(payload).toEqual([
      { question_id: "q1", answer: "yes" },
      { answer: "export to CSV" },
    ]);
  });

  it("an empty form submits nothing", () => {
    expect(buildAnswerPayload(pending, {}, "   ")).toEqual([]);
  });
});
