/** Pure helpers for the knowledge UI: form building, live-status reducers, risk flags, grants and highlighting. */

import { z } from "zod";

import type { Schemas } from "@/lib/api/client";

export type Space = Schemas["SpaceOut"];
export type KnowledgeDocument = Schemas["DocumentOut"];
export type DocType = Schemas["DocType"];
export type DocStatus = Schemas["DocStatus"];
export type DocTypeChoice = DocType | "AUTO";
export type OrgSpace = Schemas["OrgSpaceOut"];
export type Grant = Schemas["GrantOut"];
export type GrantIn = Schemas["GrantIn"];
export type Permission = Schemas["Permission"];
export type PrincipalType = Schemas["PrincipalType"];

export const DOC_TYPES: { value: DocType; label: string }[] = [
  { value: "CV", label: "CV / résumé" },
  { value: "JD", label: "Job description" },
  { value: "RUBRIC", label: "Rubric" },
  { value: "QUESTION_BANK", label: "Question bank" },
  { value: "KNOWLEDGE", label: "Knowledge" },
  { value: "DATASHEET", label: "Datasheet" },
  { value: "CODE", label: "Code" },
  { value: "OTHER", label: "Other" },
];

const DOC_TYPE_LABEL = new Map(DOC_TYPES.map((t) => [t.value, t.label]));

export function docTypeLabel(type: DocType): string {
  return DOC_TYPE_LABEL.get(type) ?? type;
}

/** A `<select>` value as a doc type; unknown values mean auto-detect. */
export function toDocType(value: string): DocType | null {
  return DOC_TYPES.find((t) => t.value === value)?.value ?? null;
}

export function toDocTypeChoice(value: string): DocTypeChoice {
  return toDocType(value) ?? "AUTO";
}

export function toPermission(value: string): Permission {
  return value === "CONTRIBUTE" ? "CONTRIBUTE" : "READ";
}

const DOC_STATUSES = ["PENDING", "PARSING", "EMBEDDING", "READY", "FAILED", "DELETING"] as const;
const IN_PROGRESS: ReadonlySet<DocStatus> = new Set(["PENDING", "PARSING", "EMBEDDING", "DELETING"]);

export function isInProgress(status: DocStatus): boolean {
  return IN_PROGRESS.has(status);
}

// --- upload -----------------------------------------------------------------------------------------------------

/** The multipart body of POST /api/spaces/{space_id}/documents: every file under `files`, plus `doc_type`. */
export function buildUploadForm(files: readonly File[], docType: DocTypeChoice): FormData {
  const form = new FormData();
  for (const file of files) form.append("files", file, file.name);
  form.append("doc_type", docType);
  return form;
}

/** Add newly picked files to a selection, skipping ones already in it (same name, size and modification time). */
export function mergeFiles(current: readonly File[], added: readonly File[]): File[] {
  const key = (f: File) => `${f.name}\u0000${f.size}\u0000${f.lastModified}`;
  const seen = new Set(current.map(key));
  const merged = [...current];
  for (const file of added) {
    if (seen.has(key(file))) continue;
    seen.add(key(file));
    merged.push(file);
  }
  return merged;
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** One-line outcome of an upload, for the toast. */
export function uploadSummary(result: Schemas["UploadResult"]): string {
  const parts = [`${plural(result.documents.length, "file", "files")} added`];
  if (result.duplicates.length > 0) parts.push(`${result.duplicates.length} already in this space`);
  if (result.rejected.length > 0) parts.push(`${result.rejected.length} rejected`);
  return parts.join(", ");
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// --- live status events (SSE) -----------------------------------------------------------------------------------

const documentStatusEventSchema = z.object({
  type: z.literal("document.status"),
  space_id: z.string().min(1),
  document_id: z.string().min(1),
  status: z.enum(DOC_STATUSES),
  error: z.string().nullable().optional(),
});

export type DocumentStatusEvent = z.infer<typeof documentStatusEventSchema>;

/** Parse the data of an SSE `document.status` event; anything malformed is ignored (null). */
export function parseDocumentStatusEvent(data: string): DocumentStatusEvent | null {
  let raw: unknown;
  try {
    raw = JSON.parse(data);
  } catch {
    return null;
  }
  const parsed = documentStatusEventSchema.safeParse(raw);
  return parsed.success ? parsed.data : null;
}

/**
 * Apply a status event to the cached document list of its space. Returns the same array when nothing changes
 * (unknown document, or the same status and error), so React Query keeps its reference.
 */
export function applyStatusEvent(
  documents: KnowledgeDocument[] | undefined,
  event: DocumentStatusEvent,
): KnowledgeDocument[] | undefined {
  if (!documents) return documents;
  const index = documents.findIndex((d) => d.id === event.document_id);
  if (index === -1) return documents;
  const current = documents[index];
  const error = event.error ?? null;
  if (current.status === event.status && current.error === error) return documents;
  const next = [...documents];
  next[index] = { ...current, status: event.status, error };
  return next;
}

/** Whether an event needs a full refetch: the document is unknown here, or it finished (counts and flags changed). */
export function needsRefetch(documents: KnowledgeDocument[] | undefined, event: DocumentStatusEvent): boolean {
  if (event.status === "READY" || event.status === "FAILED") return true;
  return !documents?.some((d) => d.id === event.document_id);
}

// --- risk flags -------------------------------------------------------------------------------------------------

export interface RiskFlag {
  key: "hidden_text" | "injection_patterns";
  label: string;
  help: string;
  items: string[];
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((v): v is string => typeof v === "string" && v.trim().length > 0);
}

/** The reviewer-facing risks recorded on a document by the ingest pipeline. */
export function riskFlags(flags: Record<string, unknown>): RiskFlag[] {
  const risks: RiskFlag[] = [];
  const hidden = stringList(flags.hidden_text);
  if (hidden.length > 0) {
    risks.push({
      key: "hidden_text",
      label: "Hidden text",
      help: "Text that is invisible on the page (white, tiny or off-page). It was removed from what the AI reads.",
      items: hidden,
    });
  }
  const injection = stringList(flags.injection_patterns);
  if (injection.length > 0) {
    risks.push({
      key: "injection_patterns",
      label: "Injection pattern",
      help: "Phrases that try to instruct the AI. The AI treats document text as untrusted data, never as orders.",
      items: injection,
    });
  }
  return risks;
}

/** Pages that needed OCR (informational, not a risk). */
export function ocrPages(flags: Record<string, unknown>): number[] {
  const value = flags.ocr_pages;
  if (!Array.isArray(value)) return [];
  return value.filter((v): v is number => typeof v === "number");
}

// --- search results ---------------------------------------------------------------------------------------------

export interface TextSegment {
  text: string;
  match: boolean;
}

type Range = [number, number];

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function locate(text: string, needle: string): Range[] {
  const exact = text.indexOf(needle);
  if (exact !== -1) return [[exact, exact + needle.length]];
  const words = needle.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return [];
  const loose = new RegExp(words.map(escapeRegExp).join("\\s+")).exec(text);
  if (loose) return [[loose.index, loose.index + loose[0].length]];
  // A chunk split from a large table repeats the table header, so it is not one contiguous span of its parent:
  // mark each of its lines that does appear.
  const ranges: Range[] = [];
  for (const line of new Set(needle.split("\n").map((l) => l.trim()))) {
    if (line.length < 4) continue;
    const at = text.indexOf(line);
    if (at !== -1) ranges.push([at, at + line.length]);
  }
  return ranges;
}

/** Sort and join ranges that overlap or are separated only by whitespace (consecutive lines read as one mark). */
function mergeRanges(text: string, ranges: Range[]): Range[] {
  const sorted = [...ranges].sort((a, b) => a[0] - b[0]);
  const merged: Range[] = [];
  for (const [start, end] of sorted) {
    const last = merged.at(-1);
    if (last && (start <= last[1] || !text.slice(last[1], start).trim())) last[1] = Math.max(last[1], end);
    else merged.push([start, end]);
  }
  return merged;
}

/** Split a parent chunk into plain and highlighted segments, marking the child chunk that matched the query. */
export function highlightSegments(text: string, matched: string): TextSegment[] {
  const ranges = matched.trim() ? mergeRanges(text, locate(text, matched)) : [];
  if (ranges.length === 0) return text ? [{ text, match: false }] : [];
  const segments: TextSegment[] = [];
  let cursor = 0;
  for (const [start, end] of ranges) {
    if (start > cursor) segments.push({ text: text.slice(cursor, start), match: false });
    segments.push({ text: text.slice(start, end), match: true });
    cursor = end;
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor), match: false });
  return segments;
}

export function formatPages(from: number | null, to: number | null): string | null {
  if (from == null && to == null) return null;
  if (from == null || to == null || from === to) return `p. ${from ?? to}`;
  return `pp. ${from}–${to}`;
}

// --- grants editor ----------------------------------------------------------------------------------------------

export interface GrantRow {
  /** Stable React key for the row. */
  key: string;
  /** `USER:<uuid>` or `GROUP:<uuid>`, empty while unset. */
  principal: string;
  permission: Permission;
  /** `YYYY-MM-DD` in local time, empty for no expiry. */
  expires: string;
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

/** An ISO timestamp as a local `YYYY-MM-DD` date for `<input type="date">`. */
export function isoToDateInput(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** A local `YYYY-MM-DD` date as the ISO timestamp of the end of that day (access lasts through the date shown). */
export function dateInputToIso(value: string): string | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 23, 59, 59, 999);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

export function grantsToRows(grants: readonly Grant[]): GrantRow[] {
  return grants.map((g) => ({
    key: g.id,
    principal: `${g.principal_type}:${g.principal_id}`,
    permission: g.permission ?? "READ",
    expires: isoToDateInput(g.expires_at),
  }));
}

export type RowsResult = { ok: true; grants: GrantIn[] } | { ok: false; error: string };

/** Validate the editor rows and turn them into the PUT body (which replaces every grant of the space). */
export function rowsToGrants(rows: readonly GrantRow[]): RowsResult {
  const grants: GrantIn[] = [];
  const seen = new Set<string>();
  for (const [index, row] of rows.entries()) {
    const match = /^(USER|GROUP):(.+)$/.exec(row.principal);
    if (!match) return { ok: false, error: `Row ${index + 1}: choose a member or group.` };
    if (seen.has(row.principal)) return { ok: false, error: `Row ${index + 1}: each member or group only once.` };
    seen.add(row.principal);
    const expiresAt = row.expires ? dateInputToIso(row.expires) : null;
    if (row.expires && !expiresAt) return { ok: false, error: `Row ${index + 1}: invalid expiry date.` };
    const principalType: PrincipalType = match[1] === "USER" ? "USER" : "GROUP";
    grants.push({
      principal_type: principalType,
      principal_id: match[2],
      permission: row.permission,
      expires_at: expiresAt,
    });
  }
  return { ok: true, grants };
}
