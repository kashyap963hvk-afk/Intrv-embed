/**
 * The report's practical work and STRICT-mode camera snapshots (ARCHITECTURE §14.3, §14.4, §15.1), parsed from the
 * report endpoint's free-form JSON. Missing or malformed parts read as empty; links are only kept when they are
 * http(s) URLs (they are short-lived presigned object-store links).
 */

import { z } from "zod";

const text = z.string().catch("");
const textList = z.array(z.string()).catch([]);
const count = z.number().int().nonnegative().catch(0);

function httpUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : null;
  } catch {
    return null;
  }
}

const testSchema = z.object({
  stdin: text,
  expected: text,
  actual: text,
  passed: z.boolean().catch(false),
  detail: text,
});

export type TestOutcome = z.infer<typeof testSchema>;

const resultsSchema = z.object({
  compiled: z.boolean().nullable().catch(null),
  warnings: textList,
  errors: textList,
  tests: z.array(testSchema).catch([]),
  passed: count,
  total: count,
});

export type RunResults = z.infer<typeof resultsSchema>;

const EMPTY_RESULTS: RunResults = { compiled: null, warnings: [], errors: [], tests: [], passed: 0, total: 0 };

const reviewSchema = z.object({
  found: z.array(z.object({ bug: z.number().int().positive(), quote: text })).catch([]),
  missed: z.array(z.number().int().positive()).catch([]),
  new_bugs: textList,
  fix_correct: z.boolean().nullable().catch(null),
  reasoning: z.enum(["low", "medium", "high"]).nullable().catch(null),
  summary: text,
  /** Whiteboard submissions: what the vision model saw in the diagram. */
  description: text,
});

export type CodeReview = z.infer<typeof reviewSchema>;

const submissionSchema = z.object({
  id: text,
  topic_id: text,
  task_id: text,
  kind: text,
  code: text,
  explanation: text,
  results: resultsSchema.catch(EMPTY_RESULTS),
  review: reviewSchema.nullable().catch(null),
  image_url: z.string().nullable().catch(null).transform(httpUrl),
  at: text,
});

export type PracticalSubmission = z.infer<typeof submissionSchema>;

const extrasSchema = z.object({
  practical: z.array(z.unknown()).catch([]),
  snapshots: z.array(z.unknown()).catch([]),
});

export interface ReportExtras {
  practical: PracticalSubmission[];
  snapshots: string[];
}

/** Practical submissions (in submission order) and snapshot links from the report JSON. */
export function parseReportExtras(raw: unknown): ReportExtras {
  const parsed = extrasSchema.safeParse(raw ?? {});
  if (!parsed.success) return { practical: [], snapshots: [] };
  const practical: PracticalSubmission[] = [];
  for (const entry of parsed.data.practical) {
    const submission = submissionSchema.safeParse(entry);
    if (submission.success) practical.push(submission.data);
  }
  const snapshots = parsed.data.snapshots.map(httpUrl).filter((url): url is string => url !== null);
  return { practical, snapshots };
}

// --- presentation helpers --------------------------------------------------------------------------------------

const KIND_LABEL: Record<string, string> = {
  DEBUG_SNIPPET: "Debug the code",
  WRITE_CODE: "Write code",
  WHITEBOARD: "Whiteboard diagram",
  DATASHEET_READING: "Datasheet reading",
};

export function practicalKindLabel(kind: string): string {
  if (KIND_LABEL[kind]) return KIND_LABEL[kind];
  const words = kind.replace(/_/g, " ").trim().toLowerCase();
  return words ? words[0].toUpperCase() + words.slice(1) : "Practical task";
}

export function isWhiteboard(submission: Pick<PracticalSubmission, "kind">): boolean {
  return submission.kind === "WHITEBOARD";
}

/** "3/4 tests passed", "Did not compile", or null when the code was not run. */
export function testSummary(results: RunResults): string | null {
  if (results.compiled === false) return "Did not compile";
  if (results.total > 0) return `${results.passed}/${results.total} tests passed`;
  if (results.compiled === true) return "Compiled; the task has no tests";
  return null;
}

/** Same values as the report's recommendation tones, so the same badge colours apply. */
export type TestTone = "positive" | "negative" | "neutral";

export function testTone(results: RunResults): TestTone {
  if (results.compiled === false) return "negative";
  if (results.total === 0) return "neutral";
  return results.passed === results.total ? "positive" : "negative";
}

export function fixLabel(fixCorrect: boolean | null): string {
  if (fixCorrect === true) return "Fix is correct";
  if (fixCorrect === false) return "Fix is not correct";
  return "Fix not judged";
}

/** True when the review has code-review findings (not only a whiteboard description). */
export function hasCodeReview(review: CodeReview | null): review is CodeReview {
  if (!review) return false;
  return (
    review.found.length > 0 ||
    review.missed.length > 0 ||
    review.new_bugs.length > 0 ||
    review.fix_correct !== null ||
    review.summary.trim() !== ""
  );
}

/** The plan topic's title for a submission, falling back to the task id. */
export function submissionTitle(
  submission: Pick<PracticalSubmission, "topic_id" | "task_id">,
  topics: readonly { id: string; title: string }[],
): string {
  return topics.find((t) => t.id === submission.topic_id)?.title || submission.task_id || "Practical task";
}

export const SNAPSHOT_LABEL = "Signals, not proof — for human review only.";
/** Snapshot and diagram links expire after 10 minutes on the server; refresh well before that. */
export const EXTRAS_REFRESH_MS = 8 * 60_000;
