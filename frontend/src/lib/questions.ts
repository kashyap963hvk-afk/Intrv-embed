/**
 * Pure helpers for question sets: labels, the item and analysis models, the generation form, live status events,
 * optimistic cache updates, reordering and export file names.
 */

import { z } from "zod";

import type { Schemas } from "@/lib/api/client";
import { safeFileStem } from "@/lib/download";
import type { DocType, KnowledgeDocument, Space } from "@/lib/knowledge";
import { SENIORITY, type Seniority } from "@/lib/rolepacks";

export type QuestionSet = Schemas["SetOut"];
export type QuestionSetDetail = Schemas["SetDetail"];
export type QuestionItem = Schemas["ItemOut"];
export type SetStatus = Schemas["SetStatus"];
export type CreateSetIn = Schemas["CreateSetIn"];
export type ItemUpdate = Schemas["ItemUpdate"];
export type Requirement = Schemas["Requirement"];

export const MIN_COUNT = 3;
export const MAX_COUNT = 60;
export const DEFAULT_COUNT = 20;

// --- labels -------------------------------------------------------------------------------------------------------

export const CATEGORIES = ["technical", "cv_deep_dive", "debugging", "design", "behavioral"] as const;
export type Category = (typeof CATEGORIES)[number];

const CATEGORY_LABEL: Record<Category, string> = {
  technical: "Technical",
  cv_deep_dive: "CV deep dive",
  debugging: "Debugging",
  design: "Design",
  behavioral: "Behavioral",
};

export function categoryLabel(category: string): string {
  return category in CATEGORY_LABEL ? CATEGORY_LABEL[category as Category] : category.replace(/_/g, " ");
}

/**
 * The generator's category shares when no mix is sent (backend `questions/plan.py` DEFAULT_MIX). Only used as the
 * starting point of the sliders; a set uses the server's default unless the user customises the mix.
 */
export const DEFAULT_MIX: Record<Category, number> = {
  cv_deep_dive: 0.3,
  technical: 0.35,
  debugging: 0.15,
  design: 0.1,
  behavioral: 0.1,
};

export const SENIORITY_OPTIONS: { value: Seniority; label: string }[] = SENIORITY.map((value) => ({
  value,
  label: { JUNIOR: "Junior", MID: "Mid-level", SENIOR: "Senior", STAFF: "Staff / principal" }[value],
}));

const DIFFICULTY_LABEL = ["", "Very easy", "Easy", "Medium", "Hard", "Very hard"];

export function difficultyLabel(difficulty: number): string {
  return DIFFICULTY_LABEL[difficulty] ?? `Level ${difficulty}`;
}

const STATUS_LABEL: Record<SetStatus, string> = { GENERATING: "Generating", READY: "Ready", FAILED: "Failed" };

export function statusLabel(status: SetStatus): string {
  return STATUS_LABEL[status];
}

const REQUIREMENT_LABEL: Record<Requirement["status"], string> = {
  met: "Met",
  partial: "Partial",
  missing: "Missing",
  unclear: "Unclear",
};

export function requirementLabel(status: Requirement["status"]): string {
  return REQUIREMENT_LABEL[status];
}

export function competencyName(id: string, names: Readonly<Record<string, string>>): string {
  return names[id] ?? id;
}

// --- item and analysis models --------------------------------------------------------------------------------------

const text = z.string().catch("");
const textList = z.array(z.string()).catch([]);

const itemDataSchema = z.object({
  text,
  competency: text,
  category: text,
  difficulty: z.number().int().catch(3),
  rationale: text,
  evidence_refs: textList,
  expected_signals: textList,
  red_flags: textList,
  follow_ups: textList,
  ideal_answer: text,
  citations: textList,
  source: z.enum(["generated", "role_pack"]).catch("generated"),
  regenerated: z.boolean().catch(false),
  edited: z.boolean().catch(false),
});

export type QuestionData = z.infer<typeof itemDataSchema>;

/** An item's free-form `data` as a typed question (missing fields read as empty). */
export function parseItemData(data: Readonly<Record<string, unknown>>): QuestionData {
  return itemDataSchema.parse(data);
}

const requirementSchema = z.object({
  text: z.string(),
  competency_id: z.string().nullable().catch(null),
  status: z.enum(["met", "partial", "missing", "unclear"]).catch("unclear"),
  evidence_refs: textList,
  note: text,
});

const count = z.number().int().nonnegative().catch(0);

const analysisSchema = z.object({
  gap_matrix: z
    .object({ requirements: z.array(requirementSchema).catch([]) })
    .nullable()
    .catch(null),
  plan: z.array(z.object({ competency: z.string(), category: z.string(), difficulty: z.number() })).catch([]),
  critic: z
    .object({ kept: count, rewritten: count, dropped: count, duplicates: count, topped_up: count })
    .catch({ kept: 0, rewritten: 0, dropped: 0, duplicates: 0, topped_up: 0 }),
  notes: textList,
  prompt_versions: z.record(z.string(), z.union([z.string(), z.number()])).catch({}),
});

export type Analysis = z.infer<typeof analysisSchema>;
export type GapRequirement = z.infer<typeof requirementSchema>;

/** A set's free-form `analysis` (gap matrix, plan, critic counts, notes) with safe defaults. */
export function parseAnalysis(analysis: Readonly<Record<string, unknown>>): Analysis {
  return analysisSchema.parse(analysis);
}

/** Planned questions per competency, most first. */
export function planByCompetency(plan: Analysis["plan"]): { competency: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const slot of plan) counts.set(slot.competency, (counts.get(slot.competency) ?? 0) + 1);
  return [...counts.entries()]
    .map(([competency, n]) => ({ competency, count: n }))
    .sort((a, b) => b.count - a.count || a.competency.localeCompare(b.competency));
}

export interface ResolvedRef {
  ref: string;
  /** The CV text the ref points at, or null when no profile is loaded or the ref is unknown. */
  text: string | null;
}

/** Evidence refs such as `projects[1].claims[0]` resolved against the CV profile's evidence index. */
export function resolveEvidence(
  refs: readonly string[],
  index: Readonly<Record<string, string>> | null | undefined,
): ResolvedRef[] {
  return refs.map((ref) => ({ ref, text: index?.[ref] ?? null }));
}

// --- list helpers --------------------------------------------------------------------------------------------------

/** The item ids after moving the item at `index` by `delta` places, or null when it cannot move that way. */
export function moveItem(ids: readonly string[], index: number, delta: -1 | 1): string[] | null {
  const target = index + delta;
  if (index < 0 || index >= ids.length || target < 0 || target >= ids.length) return null;
  const next = [...ids];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

/** Items sorted in the given id order (optimistic reorder), with `ord` renumbered. */
export function applyOrder(detail: QuestionSetDetail, ids: readonly string[]): QuestionSetDetail {
  const byId = new Map(detail.items.map((item) => [item.id, item]));
  const ordered = ids.flatMap((id) => {
    const item = byId.get(id);
    return item ? [item] : [];
  });
  const rest = detail.items.filter((item) => !ids.includes(item.id));
  return { ...detail, items: [...ordered, ...rest].map((item, ord) => ({ ...item, ord })) };
}

export function replaceItem(detail: QuestionSetDetail, item: QuestionItem): QuestionSetDetail {
  return { ...detail, items: detail.items.map((current) => (current.id === item.id ? item : current)) };
}

export function removeItem(detail: QuestionSetDetail, itemId: string): QuestionSetDetail {
  const items = detail.items.filter((item) => item.id !== itemId);
  return { ...detail, items, item_count: items.length };
}

export function isGenerating(set: Pick<QuestionSet, "status"> | undefined): boolean {
  return set?.status === "GENERATING";
}

// --- the generation form -------------------------------------------------------------------------------------------

export interface DocumentOption {
  id: string;
  title: string;
  spaceName: string;
}

/** READY documents of one type across the given spaces, for the CV and JD pickers (sorted by title). */
export function documentOptions(
  spaces: readonly Space[],
  documents: readonly (readonly KnowledgeDocument[] | undefined)[],
  docType: DocType,
): DocumentOption[] {
  const options: DocumentOption[] = [];
  spaces.forEach((space, i) => {
    for (const doc of documents[i] ?? []) {
      if (doc.doc_type === docType && doc.status === "READY") {
        options.push({ id: doc.id, title: doc.title, spaceName: space.name });
      }
    }
  });
  return options.sort((a, b) => a.title.localeCompare(b.title) || a.spaceName.localeCompare(b.spaceName));
}

/** Each category's share of the mix in whole percent (largest remainder, so they add up to 100 when any is set). */
export function mixPercentages(mix: Readonly<Record<Category, number>>): Record<Category, number> {
  const total = CATEGORIES.reduce((sum, c) => sum + Math.max(0, mix[c]), 0);
  const result = Object.fromEntries(CATEGORIES.map((c) => [c, 0])) as Record<Category, number>;
  if (total <= 0) return result;
  const exact = CATEGORIES.map((c) => ({ c, value: (Math.max(0, mix[c]) / total) * 100 }));
  for (const { c, value } of exact) result[c] = Math.floor(value);
  let left = 100 - CATEGORIES.reduce((sum, c) => sum + result[c], 0);
  for (const { c } of [...exact].sort((a, b) => b.value - Math.floor(b.value) - (a.value - Math.floor(a.value)))) {
    if (left <= 0) break;
    result[c] += 1;
    left -= 1;
  }
  return result;
}

export interface SetForm {
  title: string;
  cvDocumentId: string;
  jdDocumentId: string;
  rolePackId: string;
  roleVariant: string;
  count: number;
  /** "" follows the CV's estimated seniority. */
  seniority: Seniority | "";
  /** 0 follows seniority. */
  difficulty: number;
  customMix: boolean;
  mix: Record<Category, number>;
  spaceIds: string[];
}

export const EMPTY_SET_FORM: SetForm = {
  title: "",
  cvDocumentId: "",
  jdDocumentId: "",
  rolePackId: "",
  roleVariant: "",
  count: DEFAULT_COUNT,
  seniority: "",
  difficulty: 0,
  customMix: false,
  mix: DEFAULT_MIX,
  spaceIds: [],
};

export function setFormReady(form: SetForm): boolean {
  return (
    form.cvDocumentId !== "" &&
    form.rolePackId !== "" &&
    Number.isInteger(form.count) &&
    form.count >= MIN_COUNT &&
    form.count <= MAX_COUNT &&
    (!form.customMix || CATEGORIES.some((c) => form.mix[c] > 0))
  );
}

/** The POST /api/question-sets body: unset options are left out so the server's defaults apply. */
export function buildCreateSetIn(form: SetForm): CreateSetIn {
  const body: CreateSetIn = {
    cv_document_id: form.cvDocumentId,
    role_pack_id: form.rolePackId,
    count: Math.min(MAX_COUNT, Math.max(MIN_COUNT, Math.round(form.count))),
  };
  const title = form.title.trim();
  if (title) body.title = title;
  if (form.jdDocumentId) body.jd_document_id = form.jdDocumentId;
  if (form.roleVariant) body.role_variant = form.roleVariant;
  if (form.seniority) body.seniority = form.seniority;
  if (form.difficulty >= 1 && form.difficulty <= 5) body.difficulty = form.difficulty;
  if (form.customMix) {
    const mix = Object.fromEntries(CATEGORIES.filter((c) => form.mix[c] > 0).map((c) => [c, form.mix[c]]));
    if (Object.keys(mix).length > 0) body.mix = mix;
  }
  if (form.spaceIds.length > 0) body.space_ids = [...form.spaceIds];
  return body;
}

export function toggleId(ids: readonly string[], id: string): string[] {
  return ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id];
}

// --- live status events (SSE) --------------------------------------------------------------------------------------

const setStatusEventSchema = z.object({
  type: z.literal("question_set.status"),
  set_id: z.string().min(1),
  status: z.enum(["GENERATING", "READY", "FAILED"]),
  progress: z.string().catch(""),
});

export type SetStatusEvent = z.infer<typeof setStatusEventSchema>;

/** Parse the data of an SSE `question_set.status` event; anything malformed is ignored (null). */
export function parseSetStatusEvent(data: string): SetStatusEvent | null {
  let raw: unknown;
  try {
    raw = JSON.parse(data);
  } catch {
    return null;
  }
  const parsed = setStatusEventSchema.safeParse(raw);
  return parsed.success ? parsed.data : null;
}

function withEvent<T extends QuestionSet>(set: T, event: SetStatusEvent): T {
  if (set.status === event.status && set.progress === event.progress) return set;
  return { ...set, status: event.status, progress: event.progress };
}

/** Apply a status event to the cached list; the same array comes back when nothing changes. */
export function applySetEventToList(sets: QuestionSet[] | undefined, event: SetStatusEvent): QuestionSet[] | undefined {
  if (!sets) return sets;
  const index = sets.findIndex((s) => s.id === event.set_id);
  if (index === -1) return sets;
  const updated = withEvent(sets[index], event);
  if (updated === sets[index]) return sets;
  const next = [...sets];
  next[index] = updated;
  return next;
}

export function applySetEventToDetail(
  detail: QuestionSetDetail | undefined,
  event: SetStatusEvent,
): QuestionSetDetail | undefined {
  if (!detail || detail.id !== event.set_id) return detail;
  return withEvent(detail, event);
}

/** Generation finished (items, analysis or error changed): refetch instead of patching the cache. */
export function setEventNeedsRefetch(sets: QuestionSet[] | undefined, event: SetStatusEvent): boolean {
  if (event.status !== "GENERATING") return true;
  return !sets?.some((s) => s.id === event.set_id);
}

// --- export -------------------------------------------------------------------------------------------------------

export const EXPORT_FORMATS = [
  { format: "md", label: "Markdown" },
  { format: "pdf", label: "PDF" },
  { format: "docx", label: "Word" },
] as const;

export type ExportFormat = (typeof EXPORT_FORMATS)[number]["format"];

/** The file name the API gives an export (used when the browser cannot read Content-Disposition). */
export function exportFilename(title: string, format: ExportFormat): string {
  return `${safeFileStem(title, "questions")}.${format}`;
}
