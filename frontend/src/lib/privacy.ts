/**
 * Pure helpers for the owners' privacy tools (ARCHITECTURE §15): the subject search form, grouping what is held,
 * the erasure confirmation and summaries, and the retention period.
 */

import type { Schemas } from "@/lib/api/client";

export type SubjectIn = Schemas["SubjectIn"];
export type SubjectItem = Schemas["SubjectItem"];
export type EraseIn = Schemas["EraseIn"];
export type EraseOut = Schemas["EraseOut"];

/** The API accepts at most this many ids of each kind. */
export const MAX_IDS = 200;
/** Typed by the owner before anything is erased. */
export const ERASE_CONFIRMATION = "ERASE";
export const EXPORT_FILENAME = "candidate-data.json";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export interface SubjectForm {
  email: string;
  sessionIds: string;
  profileIds: string;
  documentIds: string;
}

export const EMPTY_SUBJECT_FORM: SubjectForm = { email: "", sessionIds: "", profileIds: "", documentIds: "" };

/** Ids separated by commas, spaces or new lines; duplicates removed, invalid entries reported. */
export function parseIdList(text: string): { ids: string[]; invalid: string[] } {
  const ids: string[] = [];
  const invalid: string[] = [];
  for (const part of text.split(/[\s,;]+/)) {
    const value = part.trim();
    if (!value) continue;
    if (!UUID.test(value)) invalid.push(value);
    else if (!ids.includes(value.toLowerCase())) ids.push(value.toLowerCase());
  }
  return { ids, invalid };
}

const ID_FIELDS = [
  ["sessionIds", "Interview ids"],
  ["profileIds", "Candidate profile ids"],
  ["documentIds", "CV document ids"],
] as const;

/** Why the search cannot run, or null. */
export function subjectFormProblem(form: SubjectForm): string | null {
  const email = form.email.trim();
  if (email && !EMAIL.test(email)) return "That email address does not look right.";
  let anyId = false;
  for (const [key, label] of ID_FIELDS) {
    const { ids, invalid } = parseIdList(form[key]);
    if (invalid.length) return `${label}: not an id: ${invalid.slice(0, 3).join(", ")}`;
    if (ids.length > MAX_IDS) return `${label}: at most ${MAX_IDS} at a time.`;
    anyId ||= ids.length > 0;
  }
  if (!email && !anyId) return "Give the candidate's email or the ids of their interviews, profiles or CV documents.";
  return null;
}

/** The request body; call only when `subjectFormProblem` is null. */
export function buildSubjectIn(form: SubjectForm): SubjectIn {
  const body: SubjectIn = {
    session_ids: parseIdList(form.sessionIds).ids,
    profile_ids: parseIdList(form.profileIds).ids,
    document_ids: parseIdList(form.documentIds).ids,
  };
  const email = form.email.trim();
  if (email) body.email = email;
  return body;
}

/** A short description of what was searched for (shown next to the results and in the erase dialog). */
export function describeSubject(subject: SubjectIn): string {
  const parts: string[] = [];
  if (subject.email) parts.push(subject.email);
  const counts: [number, string][] = [
    [subject.session_ids?.length ?? 0, "interview id"],
    [subject.profile_ids?.length ?? 0, "profile id"],
    [subject.document_ids?.length ?? 0, "CV document id"],
  ];
  for (const [n, noun] of counts) if (n) parts.push(plural(n, noun));
  return parts.join(" + ");
}

// --- what is held ----------------------------------------------------------------------------------------------

export const KIND_ORDER = ["session", "question_set", "cv", "profile"] as const;

const KINDS: Record<string, { one: string; many: string; holds: string }> = {
  session: {
    one: "interview",
    many: "interviews",
    holds: "transcript, scores and decision, integrity signals, practical work, whiteboard images and snapshots",
  },
  question_set: { one: "question set", many: "question sets", holds: "questions generated for this candidate" },
  cv: { one: "CV document", many: "CV documents", holds: "the uploaded file, its text and its search index entries" },
  profile: {
    one: "candidate profile",
    many: "candidate profiles",
    holds: "name and contact details (encrypted), extracted skills and summary",
  },
};

export function plural(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

function kindNoun(kind: string, n: number): string {
  const known = KINDS[kind];
  if (!known) return plural(n, kind.replace(/_/g, " "));
  return `${n} ${n === 1 ? known.one : known.many}`;
}

export interface SubjectGroup {
  kind: string;
  title: string;
  holds: string;
  items: SubjectItem[];
}

/** Items grouped by kind in a stable order (interviews first, unknown kinds last). */
export function groupSubjectItems(items: readonly SubjectItem[]): SubjectGroup[] {
  const kinds = [...new Set(items.map((i) => i.kind))].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
  return kinds.map((kind) => {
    const known = KINDS[kind];
    const title = known ? known.many[0].toUpperCase() + known.many.slice(1) : kind.replace(/_/g, " ");
    return { kind, title, holds: known?.holds ?? "", items: items.filter((i) => i.kind === kind) };
  });
}

function rank(kind: string): number {
  const index = (KIND_ORDER as readonly string[]).indexOf(kind);
  return index === -1 ? KIND_ORDER.length : index;
}

/** "2 interviews, 1 CV document" (from items found or from the erase counts). */
export function countSummary(counts: Record<string, number>): string {
  const kinds = Object.keys(counts)
    .filter((k) => counts[k] > 0)
    .sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
  return kinds.map((k) => kindNoun(k, counts[k])).join(", ") || "nothing";
}

export function itemCounts(items: readonly SubjectItem[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const item of items) counts[item.kind] = (counts[item.kind] ?? 0) + 1;
  return counts;
}

/** Items the search found that the erase did not remove (already gone, or erased by someone else meanwhile). */
export function notErased(found: readonly SubjectItem[], erased: Record<string, number>): number {
  const total = Object.values(erased).reduce((sum, n) => sum + n, 0);
  return Math.max(0, found.length - total);
}

// --- erase -----------------------------------------------------------------------------------------------------

export function eraseConfirmed(typed: string): boolean {
  return typed.trim() === ERASE_CONFIRMATION;
}

/** The API keeps at most this many characters of the reason (in the audit log). */
export const ERASE_REASON_MAX = 500;

export function eraseReasonProblem(reason: string): string | null {
  return reason.trim().length > ERASE_REASON_MAX ? `The reason is limited to ${ERASE_REASON_MAX} characters.` : null;
}

/** The erase body: the searched subject, the confirmation and, when given, the reason for the audit log. */
export function buildEraseIn(subject: SubjectIn, reason = ""): EraseIn {
  const body: EraseIn = { ...subject, confirm: true };
  const trimmed = reason.trim();
  if (trimmed) body.reason = trimmed;
  return body;
}

// --- retention -------------------------------------------------------------------------------------------------

export const RETENTION_LIMITS = { min: 7, max: 3650 } as const;

export function retentionProblem(days: number): string | null {
  if (!Number.isInteger(days) || days < RETENTION_LIMITS.min || days > RETENTION_LIMITS.max) {
    return `Keep data for ${RETENTION_LIMITS.min} to ${RETENTION_LIMITS.max} days.`;
  }
  return null;
}

/** The oldest creation date that survives tonight's purge. */
export function retentionCutoff(days: number, now: Date): Date {
  return new Date(now.getTime() - days * 86_400_000);
}
