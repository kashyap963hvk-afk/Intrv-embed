/**
 * Pure helpers for live interviews: labels, settings and the create form, the plan model and its edits, live status
 * events, the report and join models, WebSocket frames folded into a transcript, reconnect backoff, answer resend,
 * typing meta, browser integrity signals and time formatting.
 */

import { z } from "zod";

import type { Schemas } from "@/lib/api/client";
import { safeFileStem } from "@/lib/download";
import type { Seniority } from "@/lib/rolepacks";

export type Interview = Schemas["InterviewOut"];
export type InterviewDetail = Schemas["InterviewDetail"];
export type InterviewStatus = Schemas["SessionStatus"];
export type CreateInterviewIn = Schemas["CreateInterviewIn"];
export type Template = Schemas["TemplateOut"];
export type TemplateIn = Schemas["TemplateIn"];
export type TemplatePatch = Schemas["TemplatePatch"];
export type PromptPreview = Schemas["PromptPreview"];
export type SimulateOut = Schemas["SimulateOut"];
export type InviteOut = Schemas["InterviewInviteOut"];
export type BrowserEvent = Schemas["BrowserEvent"];

const text = z.string().catch("");
const textList = z.array(z.string()).catch([]);
const count = z.number().catch(0);
const nullableText = z.string().nullable().catch(null);

function humanize(value: string): string {
  const words = value.replace(/_/g, " ").trim().toLowerCase();
  return words ? words[0].toUpperCase() + words.slice(1) : value;
}

function parseJson(data: string): unknown {
  try {
    return JSON.parse(data);
  } catch {
    return undefined;
  }
}

// --- status ----------------------------------------------------------------------------------------------------

export const STATUSES = ["PREPARING", "PLANNED", "LIVE", "COMPLETED", "ABORTED", "FAILED"] as const;

const STATUS_LABEL: Record<InterviewStatus, string> = {
  PREPARING: "Preparing",
  PLANNED: "Ready",
  LIVE: "Live",
  COMPLETED: "Completed",
  ABORTED: "Cancelled",
  FAILED: "Failed",
};

export function statusLabel(status: InterviewStatus): string {
  return STATUS_LABEL[status];
}

export function isPreparing(interview: Pick<Interview, "status"> | undefined): boolean {
  return interview?.status === "PREPARING";
}

/** The candidate link can be created or rotated once the plan exists and until the interview ends. */
export function canInvite(status: InterviewStatus): boolean {
  return status === "PLANNED" || status === "LIVE";
}

export function canEditPlan(status: InterviewStatus): boolean {
  return status === "PLANNED";
}

/** Retry a failed preparation, or re-plan one that is ready but not started. */
export function canPrepareAgain(status: InterviewStatus): boolean {
  return status === "FAILED" || status === "PLANNED";
}

/** LIVE ends the conversation (and grades it); PREPARING, PLANNED and FAILED cancel it. */
export function canEnd(status: InterviewStatus): boolean {
  return status === "LIVE" || status === "PLANNED" || status === "PREPARING" || status === "FAILED";
}

export function canDelete(status: InterviewStatus): boolean {
  return status !== "LIVE";
}

export function canOpenRoom(status: InterviewStatus): boolean {
  return status === "PLANNED" || status === "LIVE";
}

export function hasReport(interview: Pick<Interview, "status" | "started_at">): boolean {
  return interview.status === "COMPLETED" || (interview.status === "ABORTED" && interview.started_at !== null);
}

// --- labels ----------------------------------------------------------------------------------------------------

export const RECOMMENDATIONS = ["strong_no", "no", "lean_no", "lean_yes", "yes", "strong_yes"] as const;
export type Recommendation = (typeof RECOMMENDATIONS)[number];

const RECOMMENDATION_LABEL: Record<Recommendation, string> = {
  strong_no: "Strong no",
  no: "No",
  lean_no: "Lean no",
  lean_yes: "Lean yes",
  yes: "Yes",
  strong_yes: "Strong yes",
};

export function recommendationLabel(value: string | null | undefined): string {
  if (!value) return "—";
  return value in RECOMMENDATION_LABEL ? RECOMMENDATION_LABEL[value as Recommendation] : humanize(value);
}

export type Tone = "positive" | "negative" | "neutral";

export function recommendationTone(value: string | null | undefined): Tone {
  if (value === "yes" || value === "strong_yes" || value === "lean_yes") return "positive";
  if (value === "no" || value === "strong_no" || value === "lean_no") return "negative";
  return "neutral";
}

export const DECISIONS = ["advance", "hold", "reject"] as const;
export type Decision = (typeof DECISIONS)[number];

const DECISION_LABEL: Record<Decision, string> = { advance: "Advance", hold: "Hold", reject: "Reject" };

export function decisionLabel(value: string | null | undefined): string {
  if (!value) return "—";
  return value in DECISION_LABEL ? DECISION_LABEL[value as Decision] : humanize(value);
}

export function decisionTone(value: string | null | undefined): Tone {
  if (value === "advance") return "positive";
  if (value === "reject") return "negative";
  return "neutral";
}

const SECTION_LABEL: Record<string, string> = {
  OPENING: "Opening",
  CV_WALKTHROUGH: "CV walkthrough",
  TECHNICAL: "Technical",
  PRACTICAL: "Practical",
  BEHAVIORAL: "Behavioral",
  CANDIDATE_QUESTIONS: "Candidate questions",
  CLOSE: "Close",
};

export function sectionLabel(section: string | null | undefined): string {
  if (!section) return "—";
  return SECTION_LABEL[section] ?? humanize(section);
}

const ANSWER_QUALITY_LABEL: Record<string, string> = {
  strong: "Strong",
  adequate: "Adequate",
  shallow: "Shallow",
  incorrect: "Incorrect",
  non_answer: "Non-answer",
  dont_know: "Don't know",
  off_topic: "Off topic",
  asks_clarification: "Asks for clarification",
  meta_request: "Meta request",
  injection_attempt: "Prompt injection",
};

export function answerQualityLabel(value: string): string {
  return ANSWER_QUALITY_LABEL[value] ?? humanize(value);
}

const ACTION_LABEL: Record<string, string> = {
  probe: "Probe deeper",
  clarify: "Clarify",
  hint: "Give a hint",
  challenge: "Challenge",
  next_topic: "Next topic",
  next_section: "Next section",
  answer_candidate_question: "Answer the candidate's question",
  wrap_up: "Wrap up",
};

export function actionLabel(value: string): string {
  return ACTION_LABEL[value] ?? humanize(value);
}

const SIGNAL_LABEL: Record<string, string> = {
  tab_hidden: "Tab hidden",
  window_blur: "Window lost focus",
  fullscreen_exit: "Left full screen",
  paste: "Paste",
  copy: "Copy",
  extended_display: "Extended display",
  devtools: "Developer tools",
  split_screen: "Split screen",
  screen_share_stopped: "Screen share stopped",
  burst_typing: "Very fast typing",
  pasted_answer: "Answer mostly pasted",
  long_pause_then_fluent: "Long pause, then a long answer",
  matches_reference_text: "Close match to reference text",
  injection_attempt: "Prompt-injection attempt",
};

export function signalLabel(kind: string): string {
  return SIGNAL_LABEL[kind] ?? humanize(kind);
}

/** A one-line reading of a signal's numeric detail (sizes, speeds, overlaps); no free text is shown. */
export function signalDetail(detail: Readonly<Record<string, unknown>>): string {
  const parts: string[] = [];
  const num = (key: string) => (typeof detail[key] === "number" ? (detail[key] as number) : null);
  const chars = num("chars");
  const pasted = num("pasted_chars");
  if (pasted !== null && chars !== null) parts.push(`${pasted} of ${chars} characters pasted`);
  else if (chars !== null) parts.push(`${chars} characters`);
  const cps = num("chars_per_second");
  if (cps !== null) parts.push(`${cps} characters per second`);
  const delay = num("delay_s");
  if (delay !== null) parts.push(`${delay} s before answering`);
  const overlap = num("overlap");
  if (overlap !== null) parts.push(`${Math.round(overlap * 100)}% overlap`);
  return parts.join(" · ");
}

// --- settings --------------------------------------------------------------------------------------------------

export const STRICTNESS = ["lenient", "balanced", "strict"] as const;
export const TURN_MODES = ["auto", "single_call", "two_call"] as const;
export const INTEGRITY_MODES = ["OFF", "STANDARD", "STRICT"] as const;
export const CAMERA_MODES = ["off", "optional", "required"] as const;

export const LIMITS = {
  duration_minutes: { min: 10, max: 120 },
  follow_up_depth: { min: 0, max: 6 },
  grading_runs: { min: 1, max: 5 },
} as const;

export const STRICTNESS_LABEL: Record<(typeof STRICTNESS)[number], string> = {
  lenient: "Lenient",
  balanced: "Balanced",
  strict: "Strict",
};

export const TURN_MODE_LABEL: Record<(typeof TURN_MODES)[number], string> = {
  auto: "Automatic (by model quality)",
  single_call: "Single call (fast, needs a strong model)",
  two_call: "Two calls (controller, then reply)",
};

export const INTEGRITY_MODE_LABEL: Record<(typeof INTEGRITY_MODES)[number], string> = {
  OFF: "Off",
  STANDARD: "Standard",
  STRICT: "Strict",
};

export const CAMERA_MODE_LABEL: Record<(typeof CAMERA_MODES)[number], string> = {
  off: "Off",
  optional: "Optional",
  required: "Required",
};

const settingsSchema = z.object({
  duration_minutes: z.number().int().min(10).max(120).catch(45),
  follow_up_depth: z.number().int().min(0).max(6).catch(3),
  hints_allowed: z.boolean().catch(true),
  strictness: z.enum(STRICTNESS).catch("balanced"),
  language: z.string().min(1).catch("English"),
  turn_mode: z.enum(TURN_MODES).catch("auto"),
  integrity_mode: z.enum(INTEGRITY_MODES).catch("STANDARD"),
  camera_monitoring: z.enum(CAMERA_MODES).catch("off"),
  blind_grading: z.boolean().catch(true),
  grading_runs: z.number().int().min(1).max(5).catch(3),
  interviewer_name: z.string().nullable().catch(null),
});

export type Settings = z.infer<typeof settingsSchema>;

/** The API's InterviewSettings defaults (the organization may override the integrity mode). */
export const DEFAULT_SETTINGS: Settings = settingsSchema.parse({});

/** Stored settings (a free-form object) as typed settings; unknown or invalid fields read as the defaults. */
export function parseSettings(settings: Readonly<Record<string, unknown>> | null | undefined): Settings {
  return settingsSchema.parse(settings ?? {});
}

export function clampInt(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, Math.round(value)));
}

/**
 * Typed settings as the API's InterviewSettings body: numbers clamped to their limits, the language and interviewer
 * name trimmed (an empty name means the role pack's interviewer).
 */
export function settingsToApi(settings: Settings): Schemas["InterviewSettings"] {
  const name = settings.interviewer_name?.trim() ?? "";
  const { duration_minutes: d, follow_up_depth: f, grading_runs: g } = LIMITS;
  return {
    ...settings,
    duration_minutes: clampInt(settings.duration_minutes, d.min, d.max),
    follow_up_depth: clampInt(settings.follow_up_depth, f.min, f.max),
    grading_runs: clampInt(settings.grading_runs, g.min, g.max),
    language: settings.language.trim() || DEFAULT_SETTINGS.language,
    interviewer_name: name || null,
  };
}

/** Overrides of the template's settings for one interview: a field left undefined keeps the default. */
export type SettingsOverrides = { [K in keyof Settings]?: Settings[K] };

/** Only the overrides the user set, ready for `CreateInterviewIn.settings` (null when there are none). */
export function overridesToApi(overrides: SettingsOverrides): Record<string, unknown> | null {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(overrides)) {
    if (value === undefined || value === null) continue;
    if (key === "interviewer_name") {
      const name = String(value).trim();
      if (name) out[key] = name;
      continue;
    }
    if (key in LIMITS && typeof value === "number") {
      const { min, max } = LIMITS[key as keyof typeof LIMITS];
      out[key] = clampInt(value, min, max);
      continue;
    }
    out[key] = value;
  }
  return Object.keys(out).length > 0 ? out : null;
}

// --- the create form --------------------------------------------------------------------------------------------

export interface InterviewForm {
  title: string;
  candidateLabel: string;
  templateId: string;
  /** "" uses the template's role pack. */
  rolePackId: string;
  /** "" uses the template's (or the pack's default) variant. */
  roleVariant: string;
  cvDocumentId: string;
  jdDocumentId: string;
  questionSetId: string;
  /** "" estimates seniority from the CV. */
  seniority: Seniority | "";
  spaceIds: string[];
  overrides: SettingsOverrides;
}

export const EMPTY_INTERVIEW_FORM: InterviewForm = {
  title: "",
  candidateLabel: "",
  templateId: "",
  rolePackId: "",
  roleVariant: "",
  cvDocumentId: "",
  jdDocumentId: "",
  questionSetId: "",
  seniority: "",
  spaceIds: [],
  overrides: {},
};

/** A role pack is needed: chosen directly, or through a template that has one. */
export function interviewFormReady(form: InterviewForm, template: Pick<Template, "role_pack_id"> | undefined): boolean {
  return form.rolePackId !== "" || Boolean(template?.role_pack_id);
}

/** The POST /api/interviews body: only the fields the user set, so template and server defaults apply. */
export function buildCreateInterviewIn(form: InterviewForm): CreateInterviewIn {
  const body: CreateInterviewIn = {};
  const title = form.title.trim();
  const label = form.candidateLabel.trim();
  if (title) body.title = title;
  if (label) body.candidate_label = label;
  if (form.templateId) body.template_id = form.templateId;
  if (form.rolePackId) body.role_pack_id = form.rolePackId;
  if (form.roleVariant) body.role_variant = form.roleVariant;
  if (form.cvDocumentId) body.cv_document_id = form.cvDocumentId;
  if (form.jdDocumentId) body.jd_document_id = form.jdDocumentId;
  if (form.questionSetId) body.question_set_id = form.questionSetId;
  if (form.seniority) body.seniority = form.seniority;
  if (form.spaceIds.length > 0) body.space_ids = [...form.spaceIds];
  const settings = overridesToApi(form.overrides);
  if (settings) body.settings = settings;
  return body;
}

// --- templates -------------------------------------------------------------------------------------------------

/** Variables a persona (prompt layer L2) may use; the API fills them in per interview. */
export const PERSONA_VARIABLES = ["role_title", "candidate_first_name", "company", "seniority"] as const;
export const TEMPLATE_LIMITS = { nameMax: 120, personaMax: 6000, interviewerNameMax: 60 } as const;

export interface TemplateForm {
  name: string;
  rolePackId: string;
  /** "" is the pack's first (default) variant. */
  roleVariant: string;
  interviewerName: string;
  persona: string;
  settings: Settings;
}

export function templateForm(template: Template): TemplateForm {
  const settings = parseSettings(template.settings);
  return {
    name: template.name,
    rolePackId: template.role_pack_id ?? "",
    roleVariant: template.role_variant ?? "",
    interviewerName: settings.interviewer_name ?? "",
    persona: template.persona_prompt,
    settings,
  };
}

export function sameTemplateForm(a: TemplateForm, b: TemplateForm): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Why the template cannot be saved, or null. */
export function templateFormProblem(form: TemplateForm): string | null {
  const name = form.name.trim();
  if (!name || name.length > TEMPLATE_LIMITS.nameMax)
    return `The name needs 1 to ${TEMPLATE_LIMITS.nameMax} characters.`;
  if (form.persona.length > TEMPLATE_LIMITS.personaMax) {
    return `The persona is limited to ${TEMPLATE_LIMITS.personaMax} characters.`;
  }
  for (const [key, { min, max }] of Object.entries(LIMITS)) {
    const value = form.settings[key as keyof typeof LIMITS];
    if (!Number.isInteger(value) || value < min || value > max)
      return `Settings: ${key.replace(/_/g, " ")} must be ${min} to ${max}.`;
  }
  return null;
}

/**
 * The PATCH body. The API ignores null fields (it cannot clear a role pack), so the pack is only sent when one is
 * chosen, and "default variant" is sent as the pack's first variant so an earlier choice can be undone.
 */
export function buildTemplatePatch(form: TemplateForm, defaultVariantId: string | null): TemplatePatch {
  const interviewer = form.interviewerName.trim();
  const patch: TemplatePatch = {
    name: form.name.trim(),
    persona_prompt: form.persona,
    settings: settingsToApi({ ...form.settings, interviewer_name: interviewer || null }),
    interviewer_name: interviewer,
  };
  if (form.rolePackId) {
    patch.role_pack_id = form.rolePackId;
    const variant = form.roleVariant || defaultVariantId;
    if (variant) patch.role_variant = variant;
  }
  return patch;
}

export function buildTemplateIn(form: {
  name: string;
  rolePackId: string;
  roleVariant: string;
  shared: boolean;
}): TemplateIn {
  const body: TemplateIn = { name: form.name.trim() };
  if (form.rolePackId) body.role_pack_id = form.rolePackId;
  if (form.rolePackId && form.roleVariant) body.role_variant = form.roleVariant;
  if (form.shared) body.shared = true;
  return body;
}

// --- persona variables -----------------------------------------------------------------------------------------

/** Insert `{{variable}}` at the selection; returns the new text and where the cursor goes. */
export function insertVariable(
  value: string,
  selectionStart: number,
  selectionEnd: number,
  variable: string,
): { text: string; cursor: number } {
  const start = Math.max(0, Math.min(selectionStart, value.length));
  const end = Math.max(start, Math.min(selectionEnd, value.length));
  const token = `{{${variable}}}`;
  return { text: value.slice(0, start) + token + value.slice(end), cursor: start + token.length };
}

// --- the plan --------------------------------------------------------------------------------------------------

export const PLAN_SECTIONS = ["CV_WALKTHROUGH", "TECHNICAL", "PRACTICAL", "BEHAVIORAL"] as const;
export const TOPIC_LIMITS = { titleMin: 2, titleMax: 200, seedMin: 5, seedMax: 1200, topicsMin: 1, topicsMax: 30 };

const topicSchema = z.object({
  id: text,
  title: z.string().catch("Untitled topic"),
  section: z.enum(PLAN_SECTIONS).catch("TECHNICAL"),
  competencies: textList,
  source: z
    .object({ type: z.enum(["cv_claim", "jd_gap", "rubric", "question_set"]).catch("rubric"), ref: text })
    .catch({ type: "rubric", ref: "" }),
  rationale: text,
  seed_question: text,
  probe_angles: textList,
  expected_signals: textList,
  red_flags: textList,
  planned_minutes: z.number().catch(5),
  /** The role pack's practical task for a PRACTICAL topic (kept on edits so the task stays linked). */
  task_id: z.string().min(1).nullable().catch(null),
});

export type PlanTopic = z.infer<typeof topicSchema>;

const planSchema = z.object({
  topics: z.array(topicSchema).catch([]),
  structure: z.array(z.object({ section: z.string(), minutes: z.number().catch(0) })).catch([]),
  source: z.enum(["planner", "fallback"]).nullable().catch(null),
  candidate: z
    .object({ summary: text, evidence: z.record(z.string(), z.string()).catch({}) })
    .catch({ summary: "", evidence: {} }),
  context_documents: textList,
});

export type Plan = z.infer<typeof planSchema>;

export function parsePlan(plan: Readonly<Record<string, unknown>> | null | undefined): Plan {
  return planSchema.parse(plan ?? {});
}

export interface PlanSection {
  section: string;
  minutes: number | null;
  /** Topics of this section with their index in the whole plan. */
  topics: { topic: PlanTopic; index: number }[];
}

/** Topics grouped by section, in the structure's order (then any section the structure does not list). */
export function planSections(plan: Pick<Plan, "topics" | "structure">): PlanSection[] {
  const order = plan.structure.map((s) => s.section);
  for (const topic of plan.topics) if (!order.includes(topic.section)) order.push(topic.section);
  return order
    .map((section) => ({
      section,
      minutes: plan.structure.find((s) => s.section === section)?.minutes ?? null,
      topics: plan.topics.flatMap((topic, index) => (topic.section === section ? [{ topic, index }] : [])),
    }))
    .filter((group) => group.topics.length > 0);
}

/** Swap a topic with its nearest neighbour of the same section; null when it cannot move that way. */
export function moveTopic(topics: readonly PlanTopic[], index: number, delta: -1 | 1): PlanTopic[] | null {
  const topic = topics[index];
  if (!topic) return null;
  let target = index + delta;
  while (target >= 0 && target < topics.length && topics[target].section !== topic.section) target += delta;
  if (target < 0 || target >= topics.length) return null;
  const next = [...topics];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

/** The plan without one topic; null when it is the last one (a plan needs at least one). */
export function removeTopic(topics: readonly PlanTopic[], index: number): PlanTopic[] | null {
  if (topics.length <= TOPIC_LIMITS.topicsMin || index < 0 || index >= topics.length) return null;
  return topics.filter((_, i) => i !== index);
}

export function updateTopic(
  topics: readonly PlanTopic[],
  index: number,
  patch: Partial<Pick<PlanTopic, "title" | "seed_question">>,
): PlanTopic[] {
  return topics.map((topic, i) => (i === index ? { ...topic, ...patch } : topic));
}

/** Why an edited title / seed question cannot be saved, or null. */
export function topicEditProblem(title: string, seedQuestion: string): string | null {
  const t = title.trim();
  const q = seedQuestion.trim();
  if (t.length < TOPIC_LIMITS.titleMin || t.length > TOPIC_LIMITS.titleMax) {
    return `The title needs ${TOPIC_LIMITS.titleMin} to ${TOPIC_LIMITS.titleMax} characters.`;
  }
  if (q.length < TOPIC_LIMITS.seedMin || q.length > TOPIC_LIMITS.seedMax) {
    return `The seed question needs ${TOPIC_LIMITS.seedMin} to ${TOPIC_LIMITS.seedMax} characters.`;
  }
  return null;
}

/** The PATCH /plan body. */
export function planPatch(topics: readonly PlanTopic[]): Schemas["PlanIn"] {
  return { topics: { topics: topics.map((topic) => ({ ...topic })) } };
}

// --- live status events (SSE) ----------------------------------------------------------------------------------

const statusEventSchema = z.object({
  type: z.literal("interview.status"),
  session_id: z.string().min(1),
  status: z.enum(STATUSES),
  progress: z.string().catch(""),
});

export type StatusEvent = z.infer<typeof statusEventSchema>;

const reportEventSchema = z.object({
  type: z.literal("interview.report"),
  session_id: z.string().min(1),
  status: z.enum(["READY", "FAILED"]),
});

export type ReportEvent = z.infer<typeof reportEventSchema>;

export function parseStatusEvent(data: string): StatusEvent | null {
  const parsed = statusEventSchema.safeParse(parseJson(data));
  return parsed.success ? parsed.data : null;
}

export function parseReportEvent(data: string): ReportEvent | null {
  const parsed = reportEventSchema.safeParse(parseJson(data));
  return parsed.success ? parsed.data : null;
}

function withStatus<T extends Interview>(interview: T, event: StatusEvent): T {
  if (interview.status === event.status && interview.progress === event.progress) return interview;
  return { ...interview, status: event.status, progress: event.progress };
}

/** Apply a status event to the cached list; the same array comes back when nothing changes. */
export function applyStatusEventToList(list: Interview[] | undefined, event: StatusEvent): Interview[] | undefined {
  if (!list) return list;
  const index = list.findIndex((i) => i.id === event.session_id);
  if (index === -1) return list;
  const updated = withStatus(list[index], event);
  if (updated === list[index]) return list;
  const next = [...list];
  next[index] = updated;
  return next;
}

export function applyStatusEventToDetail(
  detail: InterviewDetail | undefined,
  event: StatusEvent,
): InterviewDetail | undefined {
  if (!detail || detail.id !== event.session_id) return detail;
  return withStatus(detail, event);
}

/** Preparation finished (plan, error or settings changed) or the interview is unknown: refetch. */
export function statusEventNeedsRefetch(list: Interview[] | undefined, event: StatusEvent): boolean {
  if (event.status !== "PREPARING") return true;
  return !list?.some((i) => i.id === event.session_id);
}

// --- invite ----------------------------------------------------------------------------------------------------

export const INVITE_EXPIRY = [
  { hours: 24, label: "1 day" },
  { hours: 72, label: "3 days" },
  { hours: 168, label: "7 days" },
  { hours: 336, label: "14 days" },
  { hours: 720, label: "30 days" },
] as const;

export const DEFAULT_INVITE_HOURS = 168;

// --- report ----------------------------------------------------------------------------------------------------

export const ADVISORY_LABEL = "Advisory: requires a human decision.";
export const INTEGRITY_LABEL = "Signals, not proof. A human must review before any decision.";

const evidenceSchema = z.object({ turn_seq: z.number().int().catch(0), quote: text });

const competencySchema = z.object({
  id: text,
  name: text,
  weight: count,
  assessed: z.boolean().catch(false),
  score: z.number().nullable().catch(null),
  anchor_level: z.number().nullable().catch(null),
  anchor: nullableText,
  evidence: z.array(evidenceSchema).catch([]),
  misconceptions: textList,
  spread: count,
  needs_human_review: z.boolean().catch(false),
});

export type CompetencyResult = z.infer<typeof competencySchema>;

const narrativeSchema = z.object({
  summary: text,
  claims_verified: textList,
  claims_contradicted: textList,
  claims_untested: textList,
  strengths: textList,
  concerns: textList,
  knowledge_gaps: z.array(z.object({ topic: text, explanation: text, citations: textList })).catch([]),
  next_round_focus: textList,
  recommendation: nullableText,
  recommendation_reason: text,
});

export type Narrative = z.infer<typeof narrativeSchema>;

const scorecardDataSchema = z.object({
  label: z.string().catch(ADVISORY_LABEL),
  overall_score: z.number().nullable().catch(null),
  weight_covered: count,
  competencies: z.array(competencySchema).catch([]),
  variance_flags: textList,
  score_based_recommendation: nullableText,
  narrative: narrativeSchema.nullable().catch(null),
  narrative_source: text,
  grading_problems: textList,
});

const scorecardSchema = z.object({
  status: z.enum(["PENDING", "READY", "FAILED"]).catch("PENDING"),
  error: nullableText,
  data: scorecardDataSchema.nullable().catch(null),
  recommendation: nullableText,
  human_decision: z.enum(DECISIONS).nullable().catch(null),
  decision_notes: text,
  reviewed_at: nullableText,
});

export type Scorecard = z.infer<typeof scorecardSchema>;

export const SEVERITIES = ["info", "low", "medium", "high"] as const;

const integrityEventSchema = z.object({
  at: text,
  kind: text,
  severity: z.enum(SEVERITIES).catch("low"),
  source: text,
  turn_seq: z.number().int().nullable().catch(null),
  duration_ms: z.number().nullable().catch(null),
  detail: z.record(z.string(), z.unknown()).catch({}),
});

export type IntegrityEvent = z.infer<typeof integrityEventSchema>;

const integritySchema = z.object({
  label: z.string().min(1).catch(INTEGRITY_LABEL),
  rollup: z.string().catch("nothing notable"),
  counts: z.record(z.string(), z.number()).catch({}),
  medium_or_higher: count,
  time_away_seconds: count,
  events: z.array(integrityEventSchema).catch([]),
});

export type IntegritySummary = z.infer<typeof integritySchema>;

export const SPEAKERS = ["INTERVIEWER", "CANDIDATE", "SYSTEM"] as const;
export type Speaker = (typeof SPEAKERS)[number];

const transcriptTurnSchema = z.object({
  seq: z.number().int().catch(0),
  speaker: z.enum(SPEAKERS).catch("SYSTEM"),
  text,
  section: nullableText,
  topic_id: nullableText,
  at: text,
});

export type TranscriptTurn = z.infer<typeof transcriptTurnSchema>;

const reportSchema = z.object({
  title: text,
  candidate_label: text,
  role: text,
  status: z.enum(STATUSES).catch("COMPLETED"),
  started_at: nullableText,
  ended_at: nullableText,
  scorecard: scorecardSchema.nullable().catch(null),
  integrity: integritySchema.catch(() => integritySchema.parse({})),
  transcript: z.array(transcriptTurnSchema).catch([]),
  plan: z.object({ topics: z.array(topicSchema).catch([]), source: nullableText }).catch({ topics: [], source: null }),
});

export type Report = z.infer<typeof reportSchema>;

/** The report endpoint's free-form JSON as a typed report (missing parts read as empty). */
export function parseReport(raw: unknown): Report {
  return reportSchema.parse(raw ?? {});
}

/** Grading is queued or running: the report page polls and listens for `interview.report`. */
export function gradingInProgress(report: Pick<Report, "status" | "scorecard"> | undefined): boolean {
  if (!report) return false;
  if (report.scorecard) return report.scorecard.status === "PENDING";
  return report.status === "COMPLETED";
}

/** Transcript turns with the section heading to show above the first turn of each new section. */
export function withSectionHeadings(
  turns: readonly TranscriptTurn[],
): { turn: TranscriptTurn; heading: string | null }[] {
  let current: string | null = null;
  return turns.map((turn) => {
    const heading = turn.section && turn.section !== current ? turn.section : null;
    if (turn.section) current = turn.section;
    return { turn, heading };
  });
}

/** Competency evidence quotes as turn anchors (`#turn-12`). */
export function turnAnchor(seq: number): string {
  return `turn-${seq}`;
}

export function formatScore(score: number | null | undefined): string {
  return score === null || score === undefined ? "—" : `${score.toFixed(1)} / 10`;
}

export function formatPercent(share: number): string {
  return `${Math.round(Math.max(0, Math.min(1, share)) * 100)}%`;
}

/** Integrity counts, most frequent first, with readable labels. */
export function signalCounts(counts: Readonly<Record<string, number>>): { kind: string; label: string; n: number }[] {
  return Object.entries(counts)
    .map(([kind, n]) => ({ kind, label: signalLabel(kind), n }))
    .sort((a, b) => b.n - a.n || a.label.localeCompare(b.label));
}

export const REPORT_FORMATS = [
  { format: "pdf", label: "PDF" },
  { format: "json", label: "JSON" },
] as const;

export type ReportFormat = (typeof REPORT_FORMATS)[number]["format"];

/** The file name the API gives an export (used when the browser cannot read Content-Disposition). */
export function reportFilename(title: string, format: ReportFormat): string {
  return `${safeFileStem(title, "interview")}.${format}`;
}

// --- the candidate's join page ---------------------------------------------------------------------------------

const joinInfoSchema = z.object({
  session_id: z.string().min(1),
  title: text,
  organization: text,
  interviewer_name: z.string().min(1).catch("Alex"),
  duration_minutes: z.number().catch(45),
  mode: z.string().catch("TEXT"),
  status: z.enum(STATUSES).catch("PLANNED"),
  integrity_mode: z.enum(INTEGRITY_MODES).catch("STANDARD"),
  camera_monitoring: z.enum(CAMERA_MODES).catch("off"),
  monitoring: textList,
  retention_days: z.number().nullable().catch(null),
  contact: text,
  consent_version: text,
  consent_given: z.boolean().catch(false),
  human_alternative: text,
});

export type JoinInfo = z.infer<typeof joinInfoSchema>;

export function parseJoinInfo(raw: unknown): JoinInfo {
  const parsed = joinInfoSchema.safeParse(raw);
  if (!parsed.success) throw new Error("The interview details could not be read. Please reload the page.");
  return parsed.data;
}

// --- the interview WebSocket -----------------------------------------------------------------------------------

export const PING_INTERVAL_MS = 25_000;
export const BUSY_RETRY_MS = 2_000;
export const MAX_ANSWER_CHARS = 6_000;
/** Close code of an invalid token or session (no reconnect). */
export const CLOSE_FORBIDDEN = 4403;
/** Close code when the server is too busy to accept the socket ("try again later"): reconnect with backoff. */
export const CLOSE_TRY_AGAIN = 1013;

/** Whether the socket should reconnect after closing with `code` (only an invalid link or session is final). */
export function reconnectable(code: number): boolean {
  return code !== CLOSE_FORBIDDEN;
}

export function interviewSocketUrl(wsBaseUrl: string, sessionId: string, token: string | null): string {
  const base = `${wsBaseUrl.replace(/\/+$/, "")}/ws/interview/${encodeURIComponent(sessionId)}`;
  return token ? `${base}?token=${encodeURIComponent(token)}` : base;
}

/** Delay before reconnect attempt `attempt` (0-based): exponential from 1 s, capped at 30 s, with jitter. */
export function reconnectDelay(attempt: number, random: () => number = Math.random): number {
  const ceiling = Math.min(30_000, 1_000 * 2 ** Math.max(0, Math.min(attempt, 10)));
  return Math.round(ceiling * (0.5 + random() / 2));
}

/** A random v4 UUID: `crypto.randomUUID` where available (secure contexts), else built from random bytes. */
export function newClientId(source: Pick<Crypto, "getRandomValues"> & Partial<Pick<Crypto, "randomUUID">>): string {
  if (typeof source.randomUUID === "function") return source.randomUUID();
  const bytes = source.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

const liveStateSchema = z.object({
  status: z.enum(STATUSES).catch("PLANNED"),
  phase: z.string().catch("pending"),
  section: nullableText,
  topic_number: count,
  topic_count: count,
  elapsed_minutes: count,
  total_minutes: z.number().catch(45),
  awaiting_answer: z.boolean().catch(false),
  mode: z.string().catch("TEXT"),
});

export type LiveState = z.infer<typeof liveStateSchema>;

export function parseLiveState(live: Readonly<Record<string, unknown>> | null | undefined): LiveState {
  return liveStateSchema.parse(live ?? {});
}

const seq = z.number().int();

const frameSchema = z.discriminatedUnion("type", [
  liveStateSchema.extend({ type: z.literal("state") }),
  z.object({
    type: z.literal("transcript"),
    turns: z.array(z.object({ seq, speaker: z.enum(SPEAKERS).catch("SYSTEM"), text })).catch([]),
    last_client_id: nullableText,
  }),
  z.object({ type: z.literal("accepted"), seq, client_id: nullableText }),
  z.object({ type: z.literal("turn.start"), seq, speaker: z.string().catch("INTERVIEWER") }),
  z.object({ type: z.literal("turn.delta"), seq, text: z.string() }),
  z.object({ type: z.literal("turn.end"), seq, text: z.string(), awaiting: z.boolean().catch(false) }),
  z.object({ type: z.literal("ended"), reason: text }),
  z.object({
    type: z.literal("error"),
    code: z.string().catch("error"),
    message: z.string().catch("Something went wrong."),
  }),
  z.object({ type: z.literal("pong") }),
]);

export type ServerFrame = z.infer<typeof frameSchema>;

/** Parse one server frame; anything malformed or unknown is ignored (null). */
export function parseServerFrame(data: string): ServerFrame | null {
  return parseServerFrameData(parseJson(data));
}

/** The same for a frame that arrived already decoded (the voice service relays engine frames as RTVI messages). */
export function parseServerFrameData(data: unknown): ServerFrame | null {
  const parsed = frameSchema.safeParse(data);
  return parsed.success ? parsed.data : null;
}

export interface AnswerMeta {
  typing_ms: number;
  paste_chars: number;
  paste_count: number;
  edits: number;
  /** A practical submission this answer explains (the engine links them). */
  submission_id?: string;
}

export interface PendingAnswer {
  clientId: string;
  text: string;
  meta: AnswerMeta;
  /** The answer's seq once the server acknowledged it (`accepted`); the reply's `turn.end` clears the answer. */
  acceptedSeq: number | null;
}

export interface RoomTurn {
  key: string;
  seq: number | null;
  speaker: "INTERVIEWER" | "CANDIDATE";
  text: string;
  /** Interviewer sentences still arriving. */
  streaming: boolean;
  /** A candidate answer the server has not acknowledged yet. */
  pending: boolean;
}

export interface RoomNotice {
  code: string;
  message: string;
}

export interface RoomState {
  turns: RoomTurn[];
  live: LiveState | null;
  /** When `live` arrived (ms since epoch), to run the clock locally between frames. */
  liveAt: number | null;
  awaiting: boolean;
  pending: PendingAnswer | null;
  /** Set when the interview ended (the reason), or null. */
  ended: string | null;
  notice: RoomNotice | null;
  /** An answer the server refused (not a busy retry): its text goes back into the answer box. */
  returned: { clientId: string; text: string } | null;
}

export const INITIAL_ROOM: RoomState = {
  turns: [],
  live: null,
  liveAt: null,
  awaiting: false,
  pending: null,
  ended: null,
  notice: null,
  returned: null,
};

export type RoomAction =
  | { type: "frame"; frame: ServerFrame; now: number }
  | { type: "answer.sent"; answer: PendingAnswer }
  /** A final transcript of the candidate's speech (voice mode): it joins the answer being spoken. */
  | { type: "voice.heard"; text: string }
  | { type: "notice.clear" };

/** Errors that refer to the answer just sent (the server sends no client id with them). */
const ANSWER_ERRORS = new Set(["busy", "not_live", "not_awaiting", "invalid", "too_large"]);

function pendingTurn(answer: PendingAnswer): RoomTurn {
  return {
    key: `c-${answer.clientId}`,
    seq: answer.acceptedSeq,
    speaker: "CANDIDATE",
    text: answer.text,
    streaming: false,
    pending: answer.acceptedSeq === null,
  };
}

function upsertTurn(turns: RoomTurn[], seqNo: number, update: (turn: RoomTurn | null) => RoomTurn): RoomTurn[] {
  const index = turns.findIndex((t) => t.seq === seqNo && t.speaker === "INTERVIEWER");
  if (index === -1) return [...turns, update(null)];
  const next = [...turns];
  next[index] = update(turns[index]);
  return next;
}

function joinSentences(current: string, sentence: string): string {
  const piece = sentence.trim();
  if (!piece) return current;
  return current ? `${current} ${piece}` : piece;
}

function interviewerTurn(seqNo: number, textValue: string, streaming: boolean): RoomTurn {
  return { key: `s-${seqNo}`, seq: seqNo, speaker: "INTERVIEWER", text: textValue, streaming, pending: false };
}

/**
 * Whether an answer must be sent again after a reconnect: the server's last recorded answer is not the one this
 * page sent and never saw the interviewer reply to.
 */
export function answerToResend(pending: PendingAnswer | null, lastClientId: string | null): PendingAnswer | null {
  if (!pending) return null;
  return pending.clientId === lastClientId ? null : pending;
}

const VOICE_KEY = "v-";

/** The spoken answer still being recorded: the last turn, the candidate's, from voice and not yet accepted. */
function openVoiceTurn(turns: readonly RoomTurn[]): RoomTurn | null {
  const last = turns.at(-1);
  return last && last.speaker === "CANDIDATE" && last.key.startsWith(VOICE_KEY) && last.seq === null ? last : null;
}

function voiceHeard(state: RoomState, heard: string): RoomState {
  const piece = heard.trim();
  if (!piece) return state;
  const open = openVoiceTurn(state.turns);
  if (open) {
    return { ...state, turns: [...state.turns.slice(0, -1), { ...open, text: joinSentences(open.text, piece) }] };
  }
  const turn: RoomTurn = {
    key: `${VOICE_KEY}${state.turns.length}`,
    seq: null,
    speaker: "CANDIDATE",
    text: piece,
    streaming: false,
    pending: false,
  };
  return { ...state, turns: [...state.turns, turn] };
}

/** Fold one server frame or local action into the room's state. */
export function roomReducer(state: RoomState, action: RoomAction): RoomState {
  switch (action.type) {
    case "answer.sent":
      return {
        ...state,
        pending: action.answer,
        turns: [...state.turns, pendingTurn(action.answer)],
        awaiting: false,
        notice: null,
        returned: null,
      };
    case "voice.heard":
      return voiceHeard(state, action.text);
    case "notice.clear":
      return state.notice ? { ...state, notice: null } : state;
    case "frame":
      return applyFrame(state, action.frame, action.now);
  }
}

function applyFrame(state: RoomState, frame: ServerFrame, now: number): RoomState {
  switch (frame.type) {
    case "state": {
      const live = liveStateSchema.parse(frame);
      const over = live.status === "COMPLETED" || live.status === "ABORTED";
      return {
        ...state,
        live,
        liveAt: now,
        awaiting: live.status === "LIVE" && live.awaiting_answer && state.pending === null,
        ended: over ? (state.ended ?? (live.status === "ABORTED" ? "cancelled" : "completed")) : state.ended,
      };
    }
    case "transcript": {
      const turns: RoomTurn[] = frame.turns.flatMap((turn): RoomTurn[] =>
        turn.speaker === "SYSTEM"
          ? []
          : [
              {
                key: `s-${turn.seq}`,
                seq: turn.seq,
                speaker: turn.speaker,
                text: turn.text,
                streaming: false,
                pending: false,
              },
            ],
      );
      const pending = answerToResend(state.pending, frame.last_client_id);
      return {
        ...state,
        turns: pending ? [...turns, pendingTurn({ ...pending, acceptedSeq: null })] : turns,
        pending: pending ? { ...pending, acceptedSeq: null } : null,
        awaiting: false,
      };
    }
    case "accepted": {
      const pending = state.pending;
      if (!pending || frame.client_id !== pending.clientId) {
        // Voice answers are sent by the voice service under its own ids: the spoken answer is the one accepted.
        const open = openVoiceTurn(state.turns);
        if (!open) return state;
        return {
          ...state,
          turns: [...state.turns.slice(0, -1), { ...open, seq: frame.seq }],
          awaiting: false,
          notice: null,
        };
      }
      const key = `c-${pending.clientId}`;
      return {
        ...state,
        pending: { ...pending, acceptedSeq: frame.seq },
        turns: state.turns.map((t) => (t.key === key ? { ...t, seq: frame.seq, pending: false } : t)),
        awaiting: false,
        notice: null,
      };
    }
    case "turn.start":
      return {
        ...state,
        turns: upsertTurn(state.turns, frame.seq, () => interviewerTurn(frame.seq, "", true)),
        awaiting: false,
      };
    case "turn.delta":
      return {
        ...state,
        turns: upsertTurn(state.turns, frame.seq, (turn) =>
          interviewerTurn(frame.seq, joinSentences(turn?.text ?? "", frame.text), true),
        ),
      };
    case "turn.end":
      return {
        ...state,
        turns: upsertTurn(state.turns, frame.seq, () => interviewerTurn(frame.seq, frame.text, false)),
        awaiting: frame.awaiting,
        pending: null,
      };
    case "ended":
      return {
        ...state,
        ended: frame.reason || "completed",
        awaiting: false,
        pending: null,
        turns: state.turns.map((t) => (t.streaming ? { ...t, streaming: false } : t)),
      };
    case "error": {
      const notice = { code: frame.code, message: frame.message };
      const pending = state.pending;
      // Busy: the page sends the same message again shortly (answers, start, end); nothing to show.
      if (frame.code === "busy" && !(pending && pending.acceptedSeq === null)) return { ...state, notice: null };
      if (pending && pending.acceptedSeq === null && ANSWER_ERRORS.has(frame.code)) {
        // busy: the hook sends the same answer again shortly; anything else hands the text back to the candidate.
        if (frame.code === "busy") return { ...state, notice: null };
        const key = `c-${pending.clientId}`;
        return {
          ...state,
          notice,
          pending: null,
          turns: state.turns.filter((t) => t.key !== key),
          returned: { clientId: pending.clientId, text: pending.text },
          awaiting: frame.code === "invalid" || frame.code === "too_large",
        };
      }
      return { ...state, notice };
    }
    case "pong":
      return state;
  }
}

/** A control message the page may need to send again when the server answers "busy". */
export type ControlMessage = { type: "start" } | { type: "end" } | { type: "mode"; mode: "TEXT" | "VOICE" };

/** Whether `frame` shows the server handled the control message (so a busy retry is no longer needed). */
export function controlSettled(control: ControlMessage, frame: ServerFrame): boolean {
  if (frame.type === "error") return frame.code !== "busy"; // refused for another reason: retrying will not help
  switch (control.type) {
    case "start":
      return (
        (frame.type === "state" && frame.status !== "PLANNED") ||
        frame.type === "turn.start" ||
        frame.type === "transcript" ||
        frame.type === "ended"
      );
    case "end":
      return (
        frame.type === "ended" ||
        (frame.type === "state" && (frame.status === "COMPLETED" || frame.status === "ABORTED"))
      );
    case "mode":
      return frame.type === "state" && frame.mode === control.mode;
  }
}

/**
 * What to send again after a "busy" error (the server was out of capacity): the answer it never
 * acknowledged and a start, end or mode change it never handled.
 */
export function busyRetries(pending: PendingAnswer | null, controls: readonly ControlMessage[]): object[] {
  const messages: object[] = [];
  if (pending && pending.acceptedSeq === null) messages.push(answerMessage(pending));
  messages.push(...controls);
  return messages;
}

/** The voice service lost its engine link (not the end of the interview): the room reloads its state instead. */
export function isVoiceLinkLost(frame: ServerFrame): boolean {
  return frame.type === "ended" && frame.reason === "closed";
}

/** The client → server message for an answer (the same client_id on every resend). */
export function answerMessage(answer: PendingAnswer) {
  return { type: "answer", text: answer.text, client_id: answer.clientId, meta: answer.meta } as const;
}

/** The interviewer is working on a reply: an answer is out, or sentences are still arriving. */
export function interviewerBusy(state: Pick<RoomState, "pending" | "turns">): boolean {
  return state.pending !== null || state.turns.some((t) => t.streaming);
}

// --- typing meta -----------------------------------------------------------------------------------------------

/** Per-answer typing meta: sizes and counts only, never any clipboard content. */
export interface TypingTracker {
  startedAt: number | null;
  pasteChars: number;
  pasteCount: number;
  edits: number;
}

export const EMPTY_TYPING: TypingTracker = { startedAt: null, pasteChars: 0, pasteCount: 0, edits: 0 };

/** First keystroke starts the answer's typing clock. */
export function trackKey(tracker: TypingTracker, now: number): TypingTracker {
  return tracker.startedAt === null ? { ...tracker, startedAt: now } : tracker;
}

/** Every input event counts as one edit (and starts the clock if no key was seen, e.g. a paste via menu). */
export function trackInput(tracker: TypingTracker, now: number): TypingTracker {
  return { ...tracker, startedAt: tracker.startedAt ?? now, edits: tracker.edits + 1 };
}

export function trackPaste(tracker: TypingTracker, chars: number): TypingTracker {
  return {
    ...tracker,
    pasteChars: tracker.pasteChars + Math.max(0, Math.round(chars)),
    pasteCount: tracker.pasteCount + 1,
  };
}

export function answerMeta(tracker: TypingTracker, now: number): AnswerMeta {
  return {
    typing_ms: tracker.startedAt === null ? 0 : Math.max(0, Math.round(now - tracker.startedAt)),
    paste_chars: tracker.pasteChars,
    paste_count: tracker.pasteCount,
    edits: tracker.edits,
  };
}

/**
 * How many characters a paste inserted, from the field's length before (minus the replaced selection) and after.
 * This measures the field instead of reading the clipboard.
 */
export function pastedChars(lengthBefore: number, selectionLength: number, lengthAfter: number): number {
  return Math.max(0, lengthAfter - (lengthBefore - Math.max(0, selectionLength)));
}

// --- browser integrity signals ---------------------------------------------------------------------------------

export const INTEGRITY_BATCH = 50;
export const MAX_QUEUED_EVENTS = 500;
export const INTEGRITY_FLUSH_MS = 5_000;
/** Shorter absences (a click on the browser chrome, a notification) are not signals. */
export const MIN_ABSENCE_MS = 500;
const MAX_DURATION_MS = 3_600_000;

export interface AbsenceState {
  hiddenAt: number | null;
  blurAt: number | null;
}

export const NO_ABSENCE: AbsenceState = { hiddenAt: null, blurAt: null };

export type AbsenceSignal = "hidden" | "visible" | "blur" | "focus";

/**
 * Track time away from the interview. Switching tabs fires blur then hidden (and visible then focus on return): the
 * hidden period supersedes the blur so the same absence is not reported twice.
 */
export function absenceStep(
  state: AbsenceState,
  signal: AbsenceSignal,
  now: number,
): { state: AbsenceState; event: BrowserEvent | null } {
  const away = (kind: "tab_hidden" | "window_blur", since: number): BrowserEvent | null => {
    const duration = Math.round(now - since);
    return duration >= MIN_ABSENCE_MS ? { kind, duration_ms: Math.min(duration, MAX_DURATION_MS) } : null;
  };
  switch (signal) {
    case "hidden":
      return { state: { hiddenAt: state.hiddenAt ?? now, blurAt: null }, event: null };
    case "visible":
      return {
        state: { ...state, hiddenAt: null },
        event: state.hiddenAt === null ? null : away("tab_hidden", state.hiddenAt),
      };
    case "blur":
      if (state.hiddenAt !== null) return { state, event: null };
      return { state: { ...state, blurAt: state.blurAt ?? now }, event: null };
    case "focus":
      return {
        state: { ...state, blurAt: null },
        event: state.blurAt === null ? null : away("window_blur", state.blurAt),
      };
  }
}

export function clipboardEvent(kind: "copy" | "paste", chars: number): BrowserEvent {
  return { kind, detail: { chars: Math.max(0, Math.round(chars)) } };
}

export function enqueueEvent(queue: readonly BrowserEvent[], event: BrowserEvent): BrowserEvent[] {
  return [...queue, event].slice(-MAX_QUEUED_EVENTS);
}

/** The next batch to send (at most 50, the API's limit) and what stays queued. */
export function takeBatch(queue: readonly BrowserEvent[]): { batch: BrowserEvent[]; rest: BrowserEvent[] } {
  return { batch: queue.slice(0, INTEGRITY_BATCH), rest: queue.slice(INTEGRITY_BATCH) };
}

// --- time ------------------------------------------------------------------------------------------------------

/** Seconds as m:ss (or h:mm:ss). */
export function formatClock(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(s / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const seconds = String(s % 60).padStart(2, "0");
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${seconds}` : `${minutes}:${seconds}`;
}

/** Elapsed interview time: the server's minutes plus, while the clock runs, the time since that frame. */
export function elapsedSeconds(
  live: Pick<LiveState, "elapsed_minutes" | "status">,
  liveAt: number | null,
  now: number,
  running: boolean,
): number {
  const base = live.elapsed_minutes * 60;
  if (!running || live.status !== "LIVE" || liveAt === null) return base;
  return base + Math.max(0, (now - liveAt) / 1000);
}

/** A duration in ms as "850 ms", "4.2 s" or "3 min 5 s". */
export function formatDurationMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)} s`;
  return formatSeconds(ms / 1000);
}

/** Seconds as "45 s", "3 min 5 s" or "1 h 2 min". */
export function formatSeconds(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  if (s < 3600) {
    const rest = s % 60;
    return rest ? `${Math.floor(s / 60)} min ${rest} s` : `${Math.floor(s / 60)} min`;
  }
  const minutes = Math.floor((s % 3600) / 60);
  return minutes ? `${Math.floor(s / 3600)} h ${minutes} min` : `${Math.floor(s / 3600)} h`;
}
