/**
 * Pure helpers for practical rounds (ADR 0010): the current task as the candidate sees it (never the known bugs or a
 * reference solution, which the API does not send), run reports, submissions, diagrams and their limits.
 */

import { z } from "zod";

import { ApiError } from "@/lib/api/problem";
import type { LiveState } from "@/lib/interviews";

export const TASK_KINDS = ["DEBUG_SNIPPET", "WRITE_CODE", "WHITEBOARD", "DATASHEET_READING"] as const;
export type TaskKind = (typeof TASK_KINDS)[number];

/** The API's limits (backend/app/practical/service.py and the submit schema). */
export const PRACTICAL_LIMITS = {
  codeMax: 40_000,
  explanationMax: 6_000,
  imageMaxBytes: 3 * 1024 * 1024,
} as const;

export const DIAGRAM_TYPES = ["image/png", "image/jpeg", "image/webp"] as const;

const text = z.string().catch("");
const textList = z.array(z.string()).catch([]);
const count = z.number().int().nonnegative().catch(0);

const referenceSchema = z.object({ document_id: text, title: text, text });

const taskSchema = z.object({
  task_id: z.string().min(1),
  kind: z.enum(TASK_KINDS),
  language: z.string().catch("none"),
  prompt: text,
  starter_code: text,
  test_count: count,
  can_run: z.boolean().catch(false),
  reference: z.array(referenceSchema).catch([]),
});

export type PracticalTask = z.infer<typeof taskSchema>;
export type DatasheetExcerpt = z.infer<typeof referenceSchema>;

export function parsePracticalTask(raw: unknown): PracticalTask {
  const parsed = taskSchema.safeParse(raw);
  if (!parsed.success) throw new Error("The task could not be read. Please reload the page.");
  return parsed.data;
}

const testResultSchema = z.object({
  stdin: text,
  expected: text,
  actual: text,
  passed: z.boolean().catch(false),
  detail: text,
});

const runReportSchema = z.object({
  compiled: z.boolean().catch(false),
  warnings: textList,
  errors: textList,
  tests: z.array(testResultSchema).catch([]),
  passed: count,
  total: count,
});

export type TestResult = z.infer<typeof testResultSchema>;
export type RunReport = z.infer<typeof runReportSchema>;

export function parseRunReport(raw: unknown): RunReport {
  const parsed = runReportSchema.safeParse(raw);
  if (!parsed.success) throw new Error("The run results could not be read.");
  return parsed.data;
}

const submitSchema = z.object({
  submission_id: z.uuid(),
  // Empty when the code could not be run here.
  results: z.unknown().transform((value) => {
    const report = runReportSchema.safeParse(value);
    return report.success && typeof value === "object" && value !== null && "compiled" in value ? report.data : null;
  }),
  reviewed: z.boolean().catch(false),
});

export type SubmitResult = z.infer<typeof submitSchema>;

export function parseSubmitResult(raw: unknown): SubmitResult {
  const parsed = submitSchema.safeParse(raw);
  if (!parsed.success) throw new Error("The submission could not be confirmed. Please try again.");
  return parsed.data;
}

const whiteboardSchema = z.object({
  submission_id: z.uuid(),
  vision_available: z.boolean().catch(false),
  message: z.string().nullable().catch(null),
});

export type WhiteboardResult = z.infer<typeof whiteboardSchema>;

export function parseWhiteboardResult(raw: unknown): WhiteboardResult {
  const parsed = whiteboardSchema.safeParse(raw);
  if (!parsed.success) throw new Error("The diagram upload could not be confirmed. Please try again.");
  return parsed.data;
}

// --- the room ----------------------------------------------------------------------------------------------------

/** The interview is on a practical topic (the state frame carries the section, never the task itself). */
export function isPracticalTopic(live: Pick<LiveState, "status" | "phase" | "section"> | null): boolean {
  return live !== null && live.status === "LIVE" && live.phase === "topic" && live.section === "PRACTICAL";
}

export function isCodeTask(kind: TaskKind): boolean {
  return kind === "DEBUG_SNIPPET" || kind === "WRITE_CODE";
}

const KIND_LABEL: Record<TaskKind, string> = {
  DEBUG_SNIPPET: "Debug the code",
  WRITE_CODE: "Write code",
  WHITEBOARD: "Draw a diagram",
  DATASHEET_READING: "Read the datasheet",
};

export function taskKindLabel(kind: TaskKind): string {
  return KIND_LABEL[kind];
}

const EDITOR_LANGUAGE: Record<string, string> = { c: "c", cpp: "cpp", python: "python", rust: "rust" };

/** The editor's language id for the task's language (anything else is plain text). */
export function editorLanguage(language: string): string {
  return EDITOR_LANGUAGE[language.toLowerCase()] ?? "plaintext";
}

const LANGUAGE_LABEL: Record<string, string> = { c: "C", cpp: "C++", python: "Python", rust: "Rust" };

export function languageLabel(language: string): string {
  return LANGUAGE_LABEL[language.toLowerCase()] ?? "";
}

/** One line for a run: compile status, tests and warnings. */
export function runSummary(report: RunReport): string {
  if (!report.compiled) {
    const n = report.errors.length;
    return n ? `Did not compile: ${n} error${n === 1 ? "" : "s"}.` : "Did not compile.";
  }
  const parts = ["Compiled"];
  if (report.total > 0) parts.push(`${report.passed} of ${report.total} tests passed`);
  const w = report.warnings.length;
  parts.push(w ? `${w} warning${w === 1 ? "" : "s"}` : "no warnings");
  return `${parts.join(", ")}.`;
}

export const EXPLAIN_INSTEAD = "Explain your solution instead.";

/**
 * A message for a failed run or submit. 409 means there is no task right now or the code runner is unavailable:
 * the candidate explains the solution in words instead.
 */
export function practicalErrorMessage(error: unknown): { message: string; explainInstead: boolean } {
  if (error instanceof ApiError && error.problem.status === 409) {
    const detail = error.message.trim();
    const mentions = /explain/i.test(detail);
    return { message: mentions ? detail : `${detail} ${EXPLAIN_INSTEAD}`.trim(), explainInstead: true };
  }
  const message = error instanceof Error && error.message ? error.message : "Something went wrong. Please try again.";
  return { message, explainInstead: false };
}

/** Why a diagram file cannot be sent, or null when it can. */
export function diagramProblem(file: Pick<File, "type" | "size">): string | null {
  if (!(DIAGRAM_TYPES as readonly string[]).includes(file.type)) return "Upload a PNG, JPEG or WebP image.";
  if (file.size > PRACTICAL_LIMITS.imageMaxBytes) return "The image is larger than 3 MB. Please send a smaller one.";
  if (file.size === 0) return "The image is empty.";
  return null;
}

/** The explanation answer that links a submission to the conversation. */
export function explanationProblem(explanation: string): string | null {
  const trimmed = explanation.trim();
  if (!trimmed) return "Explain what you changed and why before you submit.";
  if (trimmed.length > PRACTICAL_LIMITS.explanationMax) {
    return `Keep the explanation under ${PRACTICAL_LIMITS.explanationMax} characters.`;
  }
  return null;
}

// --- the owner's preview -----------------------------------------------------------------------------------------

const packTaskSchema = z.object({
  id: z.string().min(1),
  kind: z.enum(TASK_KINDS),
  language: z.string().catch("none"),
  prompt: text,
  starter_code: text,
  tests: z.array(z.unknown()).catch([]),
});

/**
 * The task a preview shows for a plan topic, read from the role pack (only what the candidate would see: the
 * prompt and starter code; the known bugs and tests are left out). Datasheet excerpts are chosen when the
 * interview is prepared and are not part of the pack, so a preview shows none.
 */
export function previewTask(
  spec: Readonly<Record<string, unknown>> | null | undefined,
  taskId: string | null,
): PracticalTask | null {
  if (!taskId || !spec || !Array.isArray(spec.practical_tasks)) return null;
  for (const raw of spec.practical_tasks) {
    const parsed = packTaskSchema.safeParse(raw);
    if (parsed.success && parsed.data.id === taskId) {
      const task = parsed.data;
      return {
        task_id: task.id,
        kind: task.kind,
        language: task.language,
        prompt: task.prompt,
        starter_code: task.starter_code,
        test_count: task.tests.length,
        can_run: isCodeTask(task.kind),
        reference: [],
      };
    }
  }
  return null;
}
