import { describe, expect, it } from "vitest";

import {
  fixLabel,
  hasCodeReview,
  isWhiteboard,
  parseReportExtras,
  practicalKindLabel,
  submissionTitle,
  testSummary,
  testTone,
  type RunResults,
} from "./report-extras";

const codeSubmission = {
  id: "s1",
  topic_id: "t-debug",
  task_id: "uart-ring-buffer",
  kind: "DEBUG_SNIPPET",
  code: "volatile uint8_t head;",
  explanation: "head was not volatile",
  results: {
    compiled: true,
    warnings: ["warning: unused variable 'x'"],
    errors: [],
    tests: [{ stdin: "1", expected: "2", actual: "2", passed: true, detail: "" }],
    passed: 1,
    total: 2,
  },
  review: {
    found: [{ bug: 1, quote: "head was not volatile" }],
    missed: [2],
    new_bugs: ["off-by-one in wrap"],
    fix_correct: false,
    reasoning: "medium",
    summary: "Found the volatile bug, missed the race.",
  },
  image_url: null,
  at: "2026-10-20T10:00:00Z",
};

const whiteboard = {
  id: "s2",
  topic_id: "t-arch",
  task_id: "boot-flow",
  kind: "WHITEBOARD",
  code: "",
  explanation: "",
  results: {},
  review: { description: "A boot flow: ROM → SPL → U-Boot → kernel." },
  image_url: "https://s3.example.com/bucket/org/x/sessions/y/whiteboard/abc?X-Amz-Signature=1",
  at: "2026-10-20T10:05:00Z",
};

describe("parseReportExtras", () => {
  it("parses code and whiteboard submissions and snapshot links", () => {
    const extras = parseReportExtras({
      title: "ignored",
      practical: [codeSubmission, whiteboard],
      snapshots: ["http://localhost:8333/snap1.jpg", "javascript:alert(1)", 42, "not a url"],
    });
    expect(extras.practical).toHaveLength(2);
    const [code, board] = extras.practical;
    expect(code.results).toMatchObject({
      compiled: true,
      passed: 1,
      total: 2,
      warnings: ["warning: unused variable 'x'"],
    });
    expect(code.review).toMatchObject({ found: [{ bug: 1 }], missed: [2], fix_correct: false, description: "" });
    expect(board.results).toEqual({ compiled: null, warnings: [], errors: [], tests: [], passed: 0, total: 0 });
    expect(board.review?.description).toContain("boot flow");
    expect(board.image_url).toContain("https://s3.example.com/");
    expect(extras.snapshots).toEqual(["http://localhost:8333/snap1.jpg"]);
  });

  it("tolerates missing, malformed and hostile data", () => {
    expect(parseReportExtras(undefined)).toEqual({ practical: [], snapshots: [] });
    expect(parseReportExtras({ practical: "x", snapshots: null })).toEqual({ practical: [], snapshots: [] });
    const [odd] = parseReportExtras({
      practical: [{ kind: "WHITEBOARD", image_url: "javascript:alert(1)", review: "bad", results: [] }],
    }).practical;
    expect(odd.image_url).toBeNull();
    expect(odd.review).toBeNull();
    expect(odd.results.total).toBe(0);
    const [badReview] = parseReportExtras({
      practical: [{ review: { found: "x", missed: [0, "y"], fix_correct: "yes", reasoning: "huge" } }],
    }).practical;
    expect(badReview.review).toMatchObject({ found: [], missed: [], fix_correct: null, reasoning: null });
  });
});

describe("presentation", () => {
  const results = (patch: Partial<RunResults>): RunResults => ({
    compiled: true,
    warnings: [],
    errors: [],
    tests: [],
    passed: 0,
    total: 0,
    ...patch,
  });

  it("summarises test results", () => {
    expect(testSummary(results({ passed: 3, total: 4 }))).toBe("3/4 tests passed");
    expect(testTone(results({ passed: 3, total: 4 }))).toBe("negative");
    expect(testTone(results({ passed: 4, total: 4 }))).toBe("positive");
    expect(testSummary(results({ compiled: false }))).toBe("Did not compile");
    expect(testTone(results({ compiled: false }))).toBe("negative");
    expect(testSummary(results({}))).toBe("Compiled; the task has no tests");
    expect(testSummary(results({ compiled: null }))).toBeNull();
    expect(testTone(results({ compiled: null }))).toBe("neutral");
  });

  it("labels kinds, fixes and titles", () => {
    expect(practicalKindLabel("DEBUG_SNIPPET")).toBe("Debug the code");
    expect(practicalKindLabel("NEW_KIND")).toBe("New kind");
    expect(isWhiteboard({ kind: "WHITEBOARD" })).toBe(true);
    expect(fixLabel(true)).toBe("Fix is correct");
    expect(fixLabel(null)).toBe("Fix not judged");
    const topics = [{ id: "t-debug", title: "Debug a UART ring buffer" }];
    expect(submissionTitle({ topic_id: "t-debug", task_id: "x" }, topics)).toBe("Debug a UART ring buffer");
    expect(submissionTitle({ topic_id: "other", task_id: "boot-flow" }, topics)).toBe("boot-flow");
  });

  it("tells code reviews from whiteboard descriptions", () => {
    const [code, board] = parseReportExtras({ practical: [codeSubmission, whiteboard] }).practical;
    expect(hasCodeReview(code.review)).toBe(true);
    expect(hasCodeReview(board.review)).toBe(false);
    expect(hasCodeReview(null)).toBe(false);
  });
});
