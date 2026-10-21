import { describe, expect, it } from "vitest";

import { ApiError } from "./api/problem";
import {
  diagramProblem,
  editorLanguage,
  explanationProblem,
  isCodeTask,
  isPracticalTopic,
  parsePracticalTask,
  parseRunReport,
  parseSubmitResult,
  parseWhiteboardResult,
  practicalErrorMessage,
  PRACTICAL_LIMITS,
  previewTask,
  runSummary,
} from "./practical";

const TASK = {
  task_id: "debug_isr_adc",
  kind: "DEBUG_SNIPPET",
  language: "c",
  prompt: "Find the bugs.",
  starter_code: "int main(void) { return 0; }\n",
  test_count: 0,
  can_run: true,
  reference: [],
};

const REPORT = {
  compiled: true,
  warnings: ["c:3:5: warning: unused variable 'x'"],
  errors: [],
  tests: [
    { stdin: "1\n", expected: "2\n", actual: "2\n", passed: true, detail: "" },
    { stdin: "2\n", expected: "4\n", actual: "5\n", passed: false, detail: "" },
  ],
  passed: 1,
  total: 2,
};

const ID = "0b3c7a52-9d2e-4f1a-8c6b-1e2d3f4a5b6c";

describe("the task", () => {
  it("reads the task and keeps nothing the API might add beyond the candidate's view", () => {
    const task = parsePracticalTask({ ...TASK, known_bugs: ["secret"], reference_solution: "secret" });
    expect(task).toEqual(TASK);
    expect(JSON.stringify(task)).not.toContain("secret");
  });

  it("reads datasheet excerpts and refuses a malformed task", () => {
    const task = parsePracticalTask({
      ...TASK,
      kind: "DATASHEET_READING",
      language: "none",
      reference: [{ document_id: "d1", title: "BME280", text: "CPOL=0, CPHA=0" }],
    });
    expect(task.reference).toEqual([{ document_id: "d1", title: "BME280", text: "CPOL=0, CPHA=0" }]);
    expect(() => parsePracticalTask({ ...TASK, kind: "MYSTERY" })).toThrow();
  });

  it("knows the practical topic from the live state", () => {
    const live = { status: "LIVE" as const, phase: "topic", section: "PRACTICAL" };
    expect(isPracticalTopic(live)).toBe(true);
    expect(isPracticalTopic({ ...live, section: "TECHNICAL" })).toBe(false);
    expect(isPracticalTopic({ ...live, phase: "closed" })).toBe(false);
    expect(isPracticalTopic({ ...live, status: "COMPLETED" })).toBe(false);
    expect(isPracticalTopic(null)).toBe(false);
  });

  it("maps task languages to editor languages", () => {
    expect(editorLanguage("c")).toBe("c");
    expect(editorLanguage("cpp")).toBe("cpp");
    expect(editorLanguage("Python")).toBe("python");
    expect(editorLanguage("none")).toBe("plaintext");
    expect(isCodeTask("WRITE_CODE")).toBe(true);
    expect(isCodeTask("WHITEBOARD")).toBe(false);
  });
});

describe("runs and submissions", () => {
  it("summarises a run", () => {
    const report = parseRunReport(REPORT);
    expect(runSummary(report)).toBe("Compiled, 1 of 2 tests passed, 1 warning.");
    expect(runSummary(parseRunReport({ ...REPORT, compiled: false, errors: ["e1", "e2"], tests: [] }))).toBe(
      "Did not compile: 2 errors.",
    );
    expect(
      runSummary(parseRunReport({ compiled: true, warnings: [], errors: [], tests: [], passed: 0, total: 0 })),
    ).toBe("Compiled, no warnings.");
  });

  it("reads a submission with or without run results", () => {
    expect(parseSubmitResult({ submission_id: ID, results: {}, reviewed: false })).toEqual({
      submission_id: ID,
      results: null,
      reviewed: false,
    });
    expect(parseSubmitResult({ submission_id: ID, results: REPORT, reviewed: true }).results?.passed).toBe(1);
    expect(() => parseSubmitResult({ submission_id: "nope", results: {} })).toThrow();
  });

  it("reads a diagram upload, including the no-vision message", () => {
    expect(
      parseWhiteboardResult({
        submission_id: ID,
        vision_available: false,
        message: "Diagram review needs a vision model",
      }),
    ).toEqual({ submission_id: ID, vision_available: false, message: "Diagram review needs a vision model" });
  });

  it("asks the candidate to explain instead when there is no task or no code runner (409)", () => {
    const unavailable = new ApiError({
      type: "about:blank",
      title: "Conflict",
      status: 409,
      detail: "Code can't be run on this server; explain your solution instead.",
    });
    expect(practicalErrorMessage(unavailable)).toEqual({
      message: "Code can't be run on this server; explain your solution instead.",
      explainInstead: true,
    });
    const noTask = new ApiError({
      type: "about:blank",
      title: "Conflict",
      status: 409,
      detail: "There is no practical task right now.",
    });
    expect(practicalErrorMessage(noTask)).toEqual({
      message: "There is no practical task right now. Explain your solution instead.",
      explainInstead: true,
    });
    expect(practicalErrorMessage(new Error("Network down"))).toEqual({
      message: "Network down",
      explainInstead: false,
    });
  });

  it("checks diagrams and explanations before sending", () => {
    expect(diagramProblem({ type: "image/png", size: 1024 })).toBeNull();
    expect(diagramProblem({ type: "image/gif", size: 1024 })).toMatch(/PNG, JPEG or WebP/);
    expect(diagramProblem({ type: "image/jpeg", size: PRACTICAL_LIMITS.imageMaxBytes + 1 })).toMatch(/3 MB/);
    expect(diagramProblem({ type: "image/webp", size: 0 })).toMatch(/empty/);
    expect(explanationProblem("  ")).toMatch(/Explain/);
    expect(explanationProblem("Made the flag volatile.")).toBeNull();
    expect(explanationProblem("x".repeat(PRACTICAL_LIMITS.explanationMax + 1))).toMatch(/under/);
  });
});

describe("the owner's preview", () => {
  const spec = {
    practical_tasks: [
      {
        id: "debug_isr_adc",
        kind: "DEBUG_SNIPPET",
        language: "c",
        prompt: "Find the bugs.",
        starter_code: "int x;",
        known_bugs: ["the hidden bug list"],
        tests: [{ stdin: "", expected_stdout: "" }],
      },
    ],
  };

  it("shows the prompt and starter code but never the known bugs", () => {
    const task = previewTask(spec, "debug_isr_adc");
    expect(task).toMatchObject({ task_id: "debug_isr_adc", prompt: "Find the bugs.", test_count: 1, can_run: true });
    expect(JSON.stringify(task)).not.toContain("hidden bug");
  });

  it("finds nothing for a missing task or pack", () => {
    expect(previewTask(spec, "gone")).toBeNull();
    expect(previewTask(spec, null)).toBeNull();
    expect(previewTask(undefined, "debug_isr_adc")).toBeNull();
    expect(previewTask({ practical_tasks: "nope" }, "debug_isr_adc")).toBeNull();
  });
});
