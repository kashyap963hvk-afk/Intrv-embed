import { describe, expect, it } from "vitest";

import {
  absenceStep,
  answerMessage,
  answerMeta,
  answerToResend,
  applyStatusEventToDetail,
  applyStatusEventToList,
  buildCreateInterviewIn,
  buildTemplateIn,
  buildTemplatePatch,
  canEditPlan,
  canInvite,
  clipboardEvent,
  DEFAULT_SETTINGS,
  elapsedSeconds,
  EMPTY_INTERVIEW_FORM,
  EMPTY_TYPING,
  enqueueEvent,
  formatClock,
  formatDurationMs,
  formatSeconds,
  gradingInProgress,
  INITIAL_ROOM,
  insertVariable,
  interviewerBusy,
  interviewFormReady,
  interviewSocketUrl,
  busyRetries,
  CLOSE_FORBIDDEN,
  CLOSE_TRY_AGAIN,
  controlSettled,
  reconnectable,
  isVoiceLinkLost,
  parseServerFrameData,
  MAX_QUEUED_EVENTS,
  moveTopic,
  newClientId,
  NO_ABSENCE,
  overridesToApi,
  parseJoinInfo,
  parseLiveState,
  parsePlan,
  parseReport,
  parseReportEvent,
  parseServerFrame,
  parseSettings,
  parseStatusEvent,
  pastedChars,
  planPatch,
  planSections,
  reconnectDelay,
  recommendationLabel,
  recommendationTone,
  removeTopic,
  reportFilename,
  roomReducer,
  sameTemplateForm,
  settingsToApi,
  signalCounts,
  signalDetail,
  statusEventNeedsRefetch,
  takeBatch,
  templateForm,
  templateFormProblem,
  topicEditProblem,
  trackInput,
  trackKey,
  trackPaste,
  updateTopic,
  withSectionHeadings,
  type BrowserEvent,
  type Interview,
  type InterviewDetail,
  type PendingAnswer,
  type PlanTopic,
  type RoomAction,
  type RoomState,
  type ServerFrame,
  type StatusEvent,
  type Template,
} from "./interviews";

function interview(overrides: Partial<Interview> = {}): Interview {
  return {
    id: "i1",
    title: "Sam · Firmware",
    candidate_label: "Sam",
    status: "PREPARING",
    progress: "Queued",
    error: null,
    mode: "TEXT",
    role: "Firmware engineer",
    role_pack_id: "p1",
    template_id: null,
    cv_document_id: null,
    question_set_id: null,
    consent_given: false,
    invite_expires_at: null,
    started_at: null,
    ended_at: null,
    created_at: "2026-10-20T10:00:00Z",
    ...overrides,
  };
}

function detail(overrides: Partial<InterviewDetail> = {}): InterviewDetail {
  return { ...interview(), settings: {}, persona: {}, plan: {}, live: {}, ...overrides };
}

function topic(id: string, section: PlanTopic["section"], title = `Topic ${id}`): PlanTopic {
  return {
    id,
    title,
    section,
    competencies: [],
    source: { type: "rubric", ref: "" },
    rationale: "",
    seed_question: `Tell me about ${id}.`,
    probe_angles: [],
    expected_signals: [],
    red_flags: [],
    planned_minutes: 5,
    task_id: null,
  };
}

function frame(raw: Record<string, unknown>): ServerFrame {
  const parsed = parseServerFrame(JSON.stringify(raw));
  if (!parsed) throw new Error(`unparsable frame ${JSON.stringify(raw)}`);
  return parsed;
}

function run(state: RoomState, ...frames: Record<string, unknown>[]): RoomState {
  return frames.reduce<RoomState>(
    (s, raw) => roomReducer(s, { type: "frame", frame: frame(raw), now: 1_000 } satisfies RoomAction),
    state,
  );
}

const META = { typing_ms: 1200, paste_chars: 0, paste_count: 0, edits: 12 };

function pending(clientId = "c1", text = "My answer"): PendingAnswer {
  return { clientId, text, meta: META, acceptedSeq: null };
}

const LIVE_STATE = {
  type: "state",
  status: "LIVE",
  phase: "topic",
  section: "TECHNICAL",
  topic_number: 2,
  topic_count: 6,
  elapsed_minutes: 3.5,
  total_minutes: 45,
  awaiting_answer: true,
  mode: "TEXT",
};

describe("labels and status rules", () => {
  it("labels recommendations and their direction", () => {
    expect(recommendationLabel("lean_yes")).toBe("Lean yes");
    expect(recommendationLabel("strong_no")).toBe("Strong no");
    expect(recommendationLabel(null)).toBe("—");
    expect(recommendationLabel("something_new")).toBe("Something new");
    expect(recommendationTone("yes")).toBe("positive");
    expect(recommendationTone("lean_no")).toBe("negative");
    expect(recommendationTone(undefined)).toBe("neutral");
  });

  it("allows invites and plan edits only in the right states", () => {
    expect(canInvite("PLANNED")).toBe(true);
    expect(canInvite("LIVE")).toBe(true);
    expect(canInvite("PREPARING")).toBe(false);
    expect(canEditPlan("PLANNED")).toBe(true);
    expect(canEditPlan("LIVE")).toBe(false);
  });

  it("reads integrity details as numbers only", () => {
    expect(signalDetail({ chars: 240 })).toBe("240 characters");
    expect(signalDetail({ pasted_chars: 300, chars: 400 })).toBe("300 of 400 characters pasted");
    expect(signalDetail({ overlap: 0.42, matches: ["ignore previous"] })).toBe("42% overlap");
    expect(signalCounts({ paste: 1, tab_hidden: 3 }).map((c) => c.label)).toEqual(["Tab hidden", "Paste"]);
  });
});

describe("settings", () => {
  it("reads stored settings with defaults for missing or invalid fields", () => {
    const s = parseSettings({ duration_minutes: 30, strictness: "harsh", hints_allowed: false });
    expect(s.duration_minutes).toBe(30);
    expect(s.strictness).toBe("balanced");
    expect(s.hints_allowed).toBe(false);
    expect(s.grading_runs).toBe(3);
    expect(parseSettings(null)).toEqual(DEFAULT_SETTINGS);
  });

  it("clamps numbers and trims names for the API", () => {
    const body = settingsToApi({ ...DEFAULT_SETTINGS, duration_minutes: 500, grading_runs: 0, interviewer_name: "  " });
    expect(body.duration_minutes).toBe(120);
    expect(body.grading_runs).toBe(1);
    expect(body.interviewer_name).toBeNull();
  });

  it("sends only the overrides that were set", () => {
    expect(overridesToApi({})).toBeNull();
    expect(
      overridesToApi({ duration_minutes: 200, hints_allowed: false, interviewer_name: " ", turn_mode: undefined }),
    ).toEqual({ duration_minutes: 120, hints_allowed: false });
  });
});

describe("the create form", () => {
  it("needs a role pack directly or through the template", () => {
    expect(interviewFormReady(EMPTY_INTERVIEW_FORM, undefined)).toBe(false);
    expect(interviewFormReady({ ...EMPTY_INTERVIEW_FORM, templateId: "t" }, { role_pack_id: null })).toBe(false);
    expect(interviewFormReady({ ...EMPTY_INTERVIEW_FORM, templateId: "t" }, { role_pack_id: "p" })).toBe(true);
    expect(interviewFormReady({ ...EMPTY_INTERVIEW_FORM, rolePackId: "p" }, undefined)).toBe(true);
  });

  it("leaves unset fields out of the body", () => {
    expect(buildCreateInterviewIn({ ...EMPTY_INTERVIEW_FORM, rolePackId: "p" })).toEqual({ role_pack_id: "p" });
    expect(
      buildCreateInterviewIn({
        ...EMPTY_INTERVIEW_FORM,
        title: "  ",
        candidateLabel: " Sam Lee ",
        templateId: "t",
        cvDocumentId: "cv",
        questionSetId: "qs",
        seniority: "SENIOR",
        spaceIds: ["s1"],
        overrides: { strictness: "strict" },
      }),
    ).toEqual({
      candidate_label: "Sam Lee",
      template_id: "t",
      cv_document_id: "cv",
      question_set_id: "qs",
      seniority: "SENIOR",
      space_ids: ["s1"],
      settings: { strictness: "strict" },
    });
  });
});

describe("templates", () => {
  const template: Template = {
    id: "t1",
    name: "Friendly",
    shared: false,
    mine: true,
    role_pack_id: "p1",
    role_variant: null,
    persona_prompt: "Warm.",
    settings: { duration_minutes: 30, interviewer_name: "Kai" },
    warnings: [],
    updated_at: "2026-10-20T10:00:00Z",
  };

  it("round-trips the form and detects changes", () => {
    const form = templateForm(template);
    expect(form.interviewerName).toBe("Kai");
    expect(form.settings.duration_minutes).toBe(30);
    expect(sameTemplateForm(form, templateForm(template))).toBe(true);
    expect(sameTemplateForm(form, { ...form, persona: "Cold." })).toBe(false);
  });

  it("validates the form", () => {
    const form = templateForm(template);
    expect(templateFormProblem(form)).toBeNull();
    expect(templateFormProblem({ ...form, name: " " })).toMatch(/name/);
    expect(templateFormProblem({ ...form, settings: { ...form.settings, follow_up_depth: Number.NaN } })).toMatch(
      /follow up depth/,
    );
  });

  it("sends the default variant explicitly and the interviewer name at the top level", () => {
    const patch = buildTemplatePatch({ ...templateForm(template), interviewerName: " Ada " }, "fw");
    expect(patch.role_pack_id).toBe("p1");
    expect(patch.role_variant).toBe("fw");
    expect(patch.interviewer_name).toBe("Ada");
    expect(patch.settings?.interviewer_name).toBe("Ada");
    const noPack = buildTemplatePatch({ ...templateForm(template), rolePackId: "" }, null);
    expect("role_pack_id" in noPack).toBe(false);
  });

  it("builds the create body", () => {
    expect(buildTemplateIn({ name: " A ", rolePackId: "", roleVariant: "x", shared: false })).toEqual({ name: "A" });
    expect(buildTemplateIn({ name: "A", rolePackId: "p", roleVariant: "v", shared: true })).toEqual({
      name: "A",
      role_pack_id: "p",
      role_variant: "v",
      shared: true,
    });
  });

  it("inserts persona variables at the cursor", () => {
    expect(insertVariable("Hello !", 6, 6, "candidate_first_name")).toEqual({
      text: "Hello {{candidate_first_name}}!",
      cursor: 30,
    });
    expect(insertVariable("abc", 1, 2, "company").text).toBe("a{{company}}c");
    expect(insertVariable("abc", 99, 99, "seniority").text).toBe("abc{{seniority}}");
  });
});

describe("the plan", () => {
  const topics = [
    topic("a", "CV_WALKTHROUGH"),
    topic("b", "TECHNICAL"),
    topic("c", "TECHNICAL"),
    topic("d", "BEHAVIORAL"),
  ];

  it("parses the stored plan with safe defaults", () => {
    const plan = parsePlan({ topics: [{ title: "Bare", seed_question: "Why?" }], source: "fallback" });
    expect(plan.topics[0].section).toBe("TECHNICAL");
    expect(plan.topics[0].probe_angles).toEqual([]);
    expect(plan.source).toBe("fallback");
    expect(parsePlan(undefined).topics).toEqual([]);
  });

  it("groups topics by section in the structure's order", () => {
    const sections = planSections({
      topics,
      structure: [
        { section: "OPENING", minutes: 2 },
        { section: "TECHNICAL", minutes: 20 },
        { section: "CV_WALKTHROUGH", minutes: 10 },
      ],
    });
    expect(sections.map((s) => s.section)).toEqual(["TECHNICAL", "CV_WALKTHROUGH", "BEHAVIORAL"]);
    expect(sections[0].topics.map((t) => t.index)).toEqual([1, 2]);
    expect(sections[0].minutes).toBe(20);
    expect(sections[2].minutes).toBeNull();
  });

  it("moves topics only within their section", () => {
    expect(moveTopic(topics, 1, 1)?.map((t) => t.id)).toEqual(["a", "c", "b", "d"]);
    expect(moveTopic(topics, 1, -1)).toBeNull();
    expect(moveTopic(topics, 3, -1)).toBeNull();
    expect(moveTopic(topics, 9, 1)).toBeNull();
  });

  it("keeps at least one topic", () => {
    expect(removeTopic(topics, 0)?.map((t) => t.id)).toEqual(["b", "c", "d"]);
    expect(removeTopic([topics[0]], 0)).toBeNull();
  });

  it("edits a topic and validates the edit", () => {
    const next = updateTopic(topics, 2, { title: "New", seed_question: "What happens next?" });
    expect(next[2].title).toBe("New");
    expect(next[1]).toBe(topics[1]);
    expect(topicEditProblem("A", "A real question?")).toMatch(/title/);
    expect(topicEditProblem("Fine", "Why")).toMatch(/seed question/);
    expect(topicEditProblem("Fine", "Why is that?")).toBeNull();
    expect(planPatch(next).topics.topics).toHaveLength(4);
  });

  it("keeps a practical topic's task when the plan is edited", () => {
    const plan = parsePlan({
      topics: [
        { title: "Practical: debug isr adc", section: "PRACTICAL", seed_question: "Take a look.", task_id: "t1" },
      ],
    });
    expect(plan.topics[0].task_id).toBe("t1");
    expect(parsePlan({ topics: [{ title: "Bare", seed_question: "Why?" }] }).topics[0].task_id).toBeNull();
    const edited = updateTopic(plan.topics, 0, { title: "Practical: the ADC ISR" });
    expect(planPatch(edited).topics.topics[0]).toMatchObject({ task_id: "t1", title: "Practical: the ADC ISR" });
  });
});

describe("live status events", () => {
  const event: StatusEvent = { type: "interview.status", session_id: "i1", status: "PREPARING", progress: "Planning" };

  it("parses only well-formed events", () => {
    expect(parseStatusEvent(JSON.stringify(event))).toEqual(event);
    expect(parseStatusEvent("{")).toBeNull();
    expect(parseStatusEvent(JSON.stringify({ ...event, status: "NOPE" }))).toBeNull();
    expect(parseReportEvent(JSON.stringify({ type: "interview.report", session_id: "i1", status: "READY" }))).toEqual({
      type: "interview.report",
      session_id: "i1",
      status: "READY",
    });
  });

  it("patches the list and detail caches", () => {
    const list = [interview()];
    const next = applyStatusEventToList(list, event);
    expect(next?.[0].progress).toBe("Planning");
    expect(applyStatusEventToList(next, event)).toBe(next);
    expect(applyStatusEventToList(list, { ...event, session_id: "other" })).toBe(list);
    expect(applyStatusEventToDetail(detail(), event)?.progress).toBe("Planning");
    expect(applyStatusEventToDetail(undefined, event)).toBeUndefined();
  });

  it("refetches when preparation ends or the interview is unknown", () => {
    expect(statusEventNeedsRefetch([interview()], event)).toBe(false);
    expect(statusEventNeedsRefetch([interview()], { ...event, status: "PLANNED" })).toBe(true);
    expect(statusEventNeedsRefetch([], event)).toBe(true);
  });
});

describe("the report", () => {
  it("parses the report and keeps the reviewer labels", () => {
    const report = parseReport({
      title: "Sam",
      status: "COMPLETED",
      scorecard: {
        status: "READY",
        data: {
          label: "Advisory: requires a human decision.",
          overall_score: 6.4,
          weight_covered: 0.8,
          competencies: [
            {
              id: "c",
              name: "C",
              weight: 0.5,
              assessed: true,
              score: 7,
              evidence: [{ turn_seq: 4, quote: "I used DMA" }],
            },
          ],
          narrative: { summary: "Solid", recommendation: "lean_yes" },
        },
        recommendation: "lean_yes",
        human_decision: null,
      },
      integrity: { rollup: "nothing notable", events: [{ kind: "paste", severity: "low", detail: { chars: 12 } }] },
      transcript: [{ seq: 1, speaker: "INTERVIEWER", text: "Hi" }],
    });
    expect(report.scorecard?.data?.competencies[0].evidence[0].turn_seq).toBe(4);
    expect(report.scorecard?.data?.narrative?.strengths).toEqual([]);
    expect(report.integrity.label).toBe("Signals, not proof. A human must review before any decision.");
    expect(report.integrity.events[0].turn_seq).toBeNull();
    expect(report.transcript[0].speaker).toBe("INTERVIEWER");
    expect(parseReport(null).scorecard).toBeNull();
  });

  it("knows when grading is still running", () => {
    expect(gradingInProgress({ status: "COMPLETED", scorecard: null })).toBe(true);
    expect(gradingInProgress({ status: "ABORTED", scorecard: null })).toBe(false);
    const pendingCard = parseReport({ scorecard: { status: "PENDING", data: {} } }).scorecard;
    expect(gradingInProgress({ status: "COMPLETED", scorecard: pendingCard })).toBe(true);
    const failed = parseReport({ scorecard: { status: "FAILED", error: "x" } }).scorecard;
    expect(gradingInProgress({ status: "COMPLETED", scorecard: failed })).toBe(false);
  });

  it("adds a section heading at each new section", () => {
    const turns = parseReport({
      transcript: [
        { seq: 1, speaker: "INTERVIEWER", text: "a", section: "TECHNICAL" },
        { seq: 2, speaker: "CANDIDATE", text: "b", section: "TECHNICAL" },
        { seq: 3, speaker: "SYSTEM", text: "c", section: null },
        { seq: 4, speaker: "INTERVIEWER", text: "d", section: "BEHAVIORAL" },
      ],
    }).transcript;
    expect(withSectionHeadings(turns).map((t) => t.heading)).toEqual(["TECHNICAL", null, null, "BEHAVIORAL"]);
  });

  it("names exports like the API", () => {
    expect(reportFilename("Sam Lee · Firmware", "pdf")).toBe("Sam-Lee-Firmware.pdf");
    expect(reportFilename("···", "json")).toBe("interview.json");
  });
});

describe("join info", () => {
  it("parses the candidate's join details", () => {
    const info = parseJoinInfo({
      session_id: "s",
      organization: "Acme",
      monitoring: ["Your typed answers are recorded."],
      integrity_mode: "OFF",
      retention_days: 90,
    });
    expect(info.interviewer_name).toBe("Alex");
    expect(info.integrity_mode).toBe("OFF");
    expect(info.monitoring).toEqual(["Your typed answers are recorded."]);
    expect(() => parseJoinInfo({})).toThrow();
  });
});

describe("the socket", () => {
  it("builds the URL with or without a token", () => {
    expect(interviewSocketUrl("ws://api/", "s 1", null)).toBe("ws://api/ws/interview/s%201");
    expect(interviewSocketUrl("ws://api", "s", "a+b")).toBe("ws://api/ws/interview/s?token=a%2Bb");
  });

  it("backs off exponentially with jitter and a cap", () => {
    expect(reconnectDelay(0, () => 1)).toBe(1000);
    expect(reconnectDelay(0, () => 0)).toBe(500);
    expect(reconnectDelay(3, () => 1)).toBe(8000);
    expect(reconnectDelay(20, () => 1)).toBe(30_000);
  });

  it("makes v4 client ids with or without randomUUID", () => {
    const zeros = <T extends ArrayBufferView | null>(array: T) => array;
    expect(newClientId({ randomUUID: () => "a-b-c-d-e", getRandomValues: zeros })).toBe("a-b-c-d-e");
    const id = newClientId({ getRandomValues: zeros });
    expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });

  it("ignores malformed frames", () => {
    expect(parseServerFrame("not json")).toBeNull();
    expect(parseServerFrame(JSON.stringify({ type: "mystery" }))).toBeNull();
    expect(parseServerFrame(JSON.stringify({ type: "turn.delta", seq: "x", text: "a" }))).toBeNull();
    expect(parseServerFrame(JSON.stringify({ type: "pong" }))).toEqual({ type: "pong" });
  });

  it("reads the live state with defaults", () => {
    expect(parseLiveState({ status: "LIVE", topic_count: 4 })).toMatchObject({
      status: "LIVE",
      topic_count: 4,
      total_minutes: 45,
      awaiting_answer: false,
    });
  });
});

describe("the room reducer", () => {
  it("streams an interviewer turn sentence by sentence", () => {
    const state = run(
      INITIAL_ROOM,
      { type: "turn.start", seq: 1, speaker: "INTERVIEWER" },
      { type: "turn.delta", seq: 1, text: "Hi Sam." },
      { type: "turn.delta", seq: 1, text: "Tell me about DMA." },
    );
    expect(state.turns).toEqual([
      {
        key: "s-1",
        seq: 1,
        speaker: "INTERVIEWER",
        text: "Hi Sam. Tell me about DMA.",
        streaming: true,
        pending: false,
      },
    ]);
    expect(interviewerBusy(state)).toBe(true);
    const done = run(
      state,
      { type: "turn.end", seq: 1, text: "Hi Sam. Tell me about DMA?", awaiting: true },
      LIVE_STATE,
    );
    expect(done.turns[0]).toMatchObject({ text: "Hi Sam. Tell me about DMA?", streaming: false });
    expect(done.awaiting).toBe(true);
    expect(done.live?.topic_number).toBe(2);
    expect(done.liveAt).toBe(1_000);
  });

  it("restarts a turn that starts again and creates one for a delta without a start", () => {
    const state = run(
      INITIAL_ROOM,
      { type: "turn.start", seq: 3, speaker: "INTERVIEWER" },
      { type: "turn.delta", seq: 3, text: "First try." },
      { type: "turn.start", seq: 3, speaker: "INTERVIEWER" },
      { type: "turn.delta", seq: 5, text: "Orphan." },
    );
    expect(state.turns.map((t) => [t.seq, t.text])).toEqual([
      [3, ""],
      [5, "Orphan."],
    ]);
  });

  it("tracks an answer from send to acknowledgement to reply", () => {
    const awaiting = run(INITIAL_ROOM, LIVE_STATE);
    expect(awaiting.awaiting).toBe(true);
    const sent = roomReducer(awaiting, { type: "answer.sent", answer: pending() });
    expect(sent.awaiting).toBe(false);
    expect(sent.turns.at(-1)).toMatchObject({ key: "c-c1", speaker: "CANDIDATE", pending: true, seq: null });
    const accepted = run(sent, { type: "accepted", seq: 2, client_id: "c1" });
    expect(accepted.turns.at(-1)).toMatchObject({ pending: false, seq: 2 });
    expect(accepted.pending?.acceptedSeq).toBe(2);
    expect(run(sent, { type: "accepted", seq: 2, client_id: "other" })).toBe(sent);
    const replied = run(accepted, { type: "turn.end", seq: 3, text: "Thanks. Next?", awaiting: true });
    expect(replied.pending).toBeNull();
    expect(replied.awaiting).toBe(true);
  });

  it("keeps the answer box closed while an answer is out, even if a state frame says awaiting", () => {
    const sent = roomReducer(run(INITIAL_ROOM, LIVE_STATE), { type: "answer.sent", answer: pending() });
    expect(run(sent, LIVE_STATE).awaiting).toBe(false);
  });

  it("rebuilds from the transcript and keeps an answer the server never recorded", () => {
    const sent = roomReducer(run(INITIAL_ROOM, LIVE_STATE), { type: "answer.sent", answer: pending("c2") });
    const transcript = {
      type: "transcript",
      turns: [
        { seq: 1, speaker: "INTERVIEWER", text: "Q1" },
        { seq: 2, speaker: "CANDIDATE", text: "A1" },
        { seq: 3, speaker: "SYSTEM", text: "note" },
        { seq: 4, speaker: "INTERVIEWER", text: "Q2" },
      ],
      last_client_id: "c1",
    };
    const resumed = run(sent, transcript);
    expect(resumed.turns.map((t) => t.key)).toEqual(["s-1", "s-2", "s-4", "c-c2"]);
    expect(resumed.pending?.clientId).toBe("c2");
    expect(answerToResend(sent.pending, "c1")).toEqual(sent.pending);

    const recorded = run(sent, { ...transcript, last_client_id: "c2" });
    expect(recorded.pending).toBeNull();
    expect(recorded.turns.map((t) => t.key)).toEqual(["s-1", "s-2", "s-4"]);
    expect(answerToResend(sent.pending, "c2")).toBeNull();
    expect(answerToResend(null, "c2")).toBeNull();
  });

  it("keeps a busy answer for a retry and hands back a refused one", () => {
    const sent = roomReducer(run(INITIAL_ROOM, LIVE_STATE), { type: "answer.sent", answer: pending("c3", "Draft") });
    const busy = run(sent, { type: "error", code: "busy", message: "Still processing." });
    expect(busy.pending?.clientId).toBe("c3");
    expect(busy.notice).toBeNull();
    const refused = run(sent, { type: "error", code: "not_awaiting", message: "Please wait." });
    expect(refused.pending).toBeNull();
    expect(refused.turns.some((t) => t.key === "c-c3")).toBe(false);
    expect(refused.returned).toEqual({ clientId: "c3", text: "Draft" });
    expect(refused.notice?.message).toBe("Please wait.");
    const other = run(INITIAL_ROOM, { type: "error", code: "consent_required", message: "Consent first." });
    expect(other.notice?.code).toBe("consent_required");
  });

  it("ends on an ended frame or a finished state", () => {
    const ended = run(run(INITIAL_ROOM, LIVE_STATE), { type: "ended", reason: "ended by the candidate" });
    expect(ended.ended).toBe("ended by the candidate");
    expect(ended.awaiting).toBe(false);
    expect(run(INITIAL_ROOM, { ...LIVE_STATE, status: "COMPLETED" }).ended).toBe("completed");
    expect(run(INITIAL_ROOM, { ...LIVE_STATE, status: "ABORTED" }).ended).toBe("cancelled");
    expect(run(INITIAL_ROOM, { ...LIVE_STATE, status: "PLANNED" }).ended).toBeNull();
  });

  it("builds the answer message with the same client id", () => {
    expect(answerMessage(pending("c9", "Hi"))).toEqual({ type: "answer", text: "Hi", client_id: "c9", meta: META });
  });

  it("links a practical submission through the answer's meta", () => {
    const answer = { ...pending("c7", "I made the flag volatile."), meta: { ...META, submission_id: "s-1" } };
    expect(answerMessage(answer).meta.submission_id).toBe("s-1");
  });
});

describe("a busy server", () => {
  it("swallows busy errors so the page can send the same message again", () => {
    const busy = run(INITIAL_ROOM, { type: "error", code: "busy", message: "Busy, try again." });
    expect(busy.notice).toBeNull();
  });

  it("resends the unacknowledged answer and any unhandled start, end or mode change", () => {
    expect(busyRetries(pending("c1"), [{ type: "start" }])).toEqual([answerMessage(pending("c1")), { type: "start" }]);
    expect(busyRetries({ ...pending("c1"), acceptedSeq: 4 }, [{ type: "end" }])).toEqual([{ type: "end" }]);
    expect(busyRetries(null, [])).toEqual([]);
  });

  it("knows when a start, end or mode change took effect", () => {
    const start = { type: "start" } as const;
    expect(controlSettled(start, frame({ ...LIVE_STATE, status: "PLANNED" }))).toBe(false);
    expect(controlSettled(start, frame(LIVE_STATE))).toBe(true);
    expect(controlSettled(start, frame({ type: "turn.start", seq: 1, speaker: "INTERVIEWER" }))).toBe(true);
    expect(controlSettled(start, frame({ type: "error", code: "busy", message: "" }))).toBe(false);
    expect(controlSettled(start, frame({ type: "error", code: "consent_required", message: "" }))).toBe(true);
    const end = { type: "end" } as const;
    expect(controlSettled(end, frame(LIVE_STATE))).toBe(false);
    expect(controlSettled(end, frame({ type: "ended", reason: "ended by the candidate" }))).toBe(true);
    expect(controlSettled(end, frame({ ...LIVE_STATE, status: "COMPLETED" }))).toBe(true);
    const mode = { type: "mode", mode: "TEXT" } as const;
    expect(controlSettled(mode, frame({ ...LIVE_STATE, mode: "VOICE" }))).toBe(false);
    expect(controlSettled(mode, frame(LIVE_STATE))).toBe(true);
  });

  it("reconnects after any close except an invalid link, including 1013 (try again later)", () => {
    expect(reconnectable(CLOSE_TRY_AGAIN)).toBe(true);
    expect(reconnectable(1006)).toBe(true);
    expect(reconnectable(CLOSE_FORBIDDEN)).toBe(false);
  });
});

describe("voice turns", () => {
  const voice = (state: RoomState, text: string) => roomReducer(state, { type: "voice.heard", text });

  it("builds the spoken answer from final transcripts until the engine accepts it", () => {
    const asked = run(INITIAL_ROOM, LIVE_STATE, {
      type: "turn.end",
      seq: 1,
      text: "Tell me about DMA?",
      awaiting: true,
    });
    const speaking = voice(voice(asked, "I used DMA"), " in circular mode. ");
    expect(speaking.turns.at(-1)).toMatchObject({
      speaker: "CANDIDATE",
      text: "I used DMA in circular mode.",
      seq: null,
      pending: false,
    });
    // The voice service sends the answer under its own client id.
    const accepted = run(speaking, { type: "accepted", seq: 2, client_id: "voice-1" });
    expect(accepted.turns.at(-1)).toMatchObject({ seq: 2, text: "I used DMA in circular mode." });
    expect(accepted.awaiting).toBe(false);
    // The next words start a new answer after the interviewer's reply.
    const replied = run(accepted, { type: "turn.end", seq: 3, text: "Why circular?", awaiting: true });
    const next = voice(replied, "Because of the ADC.");
    expect(next.turns.map((t) => [t.speaker, t.seq])).toEqual([
      ["INTERVIEWER", 1],
      ["CANDIDATE", 2],
      ["INTERVIEWER", 3],
      ["CANDIDATE", null],
    ]);
    expect(new Set(next.turns.map((t) => t.key)).size).toBe(next.turns.length);
    expect(voice(next, "   ")).toBe(next);
  });

  it("reads relayed frames and tells a lost voice link from the end of the interview", () => {
    expect(parseServerFrameData({ type: "ended", reason: "closed" })).toEqual({ type: "ended", reason: "closed" });
    expect(parseServerFrameData({ type: "turn.delta", seq: "x" })).toBeNull();
    expect(isVoiceLinkLost(frame({ type: "ended", reason: "closed" }))).toBe(true);
    expect(isVoiceLinkLost(frame({ type: "ended", reason: "completed" }))).toBe(false);
    expect(isVoiceLinkLost(frame({ type: "pong" }))).toBe(false);
  });
});

describe("typing meta", () => {
  it("measures from the first keystroke and counts edits and pastes", () => {
    let t = trackKey(EMPTY_TYPING, 1_000);
    t = trackKey(t, 1_500);
    t = trackInput(t, 1_600);
    t = trackPaste(trackInput(t, 2_000), 120);
    expect(answerMeta(t, 4_000)).toEqual({ typing_ms: 3_000, paste_chars: 120, paste_count: 1, edits: 2 });
    expect(answerMeta(EMPTY_TYPING, 9_000).typing_ms).toBe(0);
    expect(trackInput(EMPTY_TYPING, 50).startedAt).toBe(50);
  });

  it("measures a paste from the field, accounting for a replaced selection", () => {
    expect(pastedChars(10, 0, 30)).toBe(20);
    expect(pastedChars(10, 4, 30)).toBe(24);
    expect(pastedChars(10, 0, 5)).toBe(0);
  });
});

describe("integrity signals", () => {
  it("reports a tab switch once, as time hidden", () => {
    let state = NO_ABSENCE;
    const events: (BrowserEvent | null)[] = [];
    for (const [signal, at] of [
      ["blur", 1_000],
      ["hidden", 1_050],
      ["visible", 9_050],
      ["focus", 9_060],
    ] as const) {
      const step = absenceStep(state, signal, at);
      state = step.state;
      events.push(step.event);
    }
    expect(events.filter(Boolean)).toEqual([{ kind: "tab_hidden", duration_ms: 8_000 }]);
  });

  it("reports window blur with its duration and ignores very short ones", () => {
    const blurred = absenceStep(NO_ABSENCE, "blur", 0).state;
    expect(absenceStep(blurred, "focus", 3_000).event).toEqual({ kind: "window_blur", duration_ms: 3_000 });
    expect(absenceStep(blurred, "focus", 200).event).toBeNull();
    expect(absenceStep(NO_ABSENCE, "focus", 10).event).toBeNull();
    expect(absenceStep(NO_ABSENCE, "visible", 10).event).toBeNull();
  });

  it("caps durations at an hour", () => {
    const hidden = absenceStep(NO_ABSENCE, "hidden", 0).state;
    expect(absenceStep(hidden, "visible", 10_000_000).event?.duration_ms).toBe(3_600_000);
  });

  it("records clipboard sizes only", () => {
    expect(clipboardEvent("copy", 12.4)).toEqual({ kind: "copy", detail: { chars: 12 } });
    expect(clipboardEvent("paste", -3)).toEqual({ kind: "paste", detail: { chars: 0 } });
  });

  it("batches at most 50 events and bounds the queue", () => {
    let queue: BrowserEvent[] = [];
    for (let i = 0; i < MAX_QUEUED_EVENTS + 20; i++)
      queue = enqueueEvent(queue, { kind: "copy", detail: { chars: i } });
    expect(queue).toHaveLength(MAX_QUEUED_EVENTS);
    expect(queue[0].detail).toEqual({ chars: 20 });
    const { batch, rest } = takeBatch(queue);
    expect(batch).toHaveLength(50);
    expect(rest).toHaveLength(MAX_QUEUED_EVENTS - 50);
  });
});

describe("time", () => {
  it("formats clocks and durations", () => {
    expect(formatClock(0)).toBe("0:00");
    expect(formatClock(65.9)).toBe("1:05");
    expect(formatClock(3_725)).toBe("1:02:05");
    expect(formatDurationMs(850)).toBe("850 ms");
    expect(formatDurationMs(4_200)).toBe("4.2 s");
    expect(formatDurationMs(185_000)).toBe("3 min 5 s");
    expect(formatDurationMs(null)).toBe("—");
    expect(formatSeconds(120)).toBe("2 min");
    expect(formatSeconds(3_720)).toBe("1 h 2 min");
  });

  it("runs the clock locally between server frames", () => {
    const live = { elapsed_minutes: 2, status: "LIVE" as const };
    expect(elapsedSeconds(live, 1_000, 31_000, true)).toBe(150);
    expect(elapsedSeconds(live, 1_000, 31_000, false)).toBe(120);
    expect(elapsedSeconds({ ...live, status: "COMPLETED" }, 1_000, 31_000, true)).toBe(120);
  });
});
