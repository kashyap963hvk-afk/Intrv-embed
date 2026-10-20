/**
 * Pure helpers for role packs: grouping and permissions, the editable spec model (form ↔ spec JSON), weights,
 * a YAML emitter for the YAML tab, validation problems, Role Builder diffs and the editor draft hand-off.
 */

import { z } from "zod";

import type { Schemas } from "@/lib/api/client";
import { isProblem } from "@/lib/api/problem";

export type RolePack = Schemas["RolePackOut"];
export type RolePackDetail = Schemas["RolePackDetail"];
export type PackVisibility = Schemas["PackVisibility"];
export type PackVersion = Schemas["VersionOut"];
export type SpecProblem = Schemas["SpecProblem"];
/** The API client reads the diff's `[before, after]` weight tuples as plain arrays. */
export type PackDiff = Omit<Schemas["PackDiff"], "weight_changes"> & {
  weight_changes: Record<string, Record<string, readonly (number | null)[]>>;
};
export type BuildIn = Schemas["BuildIn"];
export type BuildOut = Omit<Schemas["BuildOut"], "diff"> & { diff: PackDiff | null };
export type PackIn = Schemas["PackIn"];
export type Validation = Schemas["ValidationOut"];

export const SENIORITY = ["JUNIOR", "MID", "SENIOR", "STAFF"] as const;
export type Seniority = (typeof SENIORITY)[number];

const SENIORITY_LABEL: Record<Seniority, string> = { JUNIOR: "Junior", MID: "Mid", SENIOR: "Senior", STAFF: "Staff" };

export function seniorityLabel(level: Seniority): string {
  return SENIORITY_LABEL[level];
}

/** The four behaviourally anchored levels every competency defines. */
export const ANCHOR_LEVELS = [1, 2, 3, 4] as const;

/** Allowed drift of a role variant's weights from 1.0 (the API's WEIGHT_TOLERANCE). */
export const WEIGHT_TOLERANCE = 0.01;

// --- list, grouping and permissions ------------------------------------------------------------------------------

export interface PackGroups {
  builtIn: RolePack[];
  organization: RolePack[];
  mine: RolePack[];
}

/** Built-in (shipped), organization (published by owners) and the caller's private packs, each sorted by name. */
export function groupPacks(packs: readonly RolePack[]): PackGroups {
  const byName = (a: RolePack, b: RolePack) => a.name.localeCompare(b.name);
  return {
    builtIn: packs.filter((p) => p.visibility === "SYSTEM").sort(byName),
    organization: packs.filter((p) => p.visibility === "ORG").sort(byName),
    mine: packs.filter((p) => p.visibility === "PRIVATE").sort(byName),
  };
}

const VISIBILITY_LABEL: Record<PackVisibility, string> = {
  SYSTEM: "built-in",
  ORG: "organization",
  PRIVATE: "private",
};

export function visibilityLabel(visibility: PackVisibility): string {
  return VISIBILITY_LABEL[visibility];
}

/** Owners can publish their own private packs to the organization. */
export function canPublish(pack: RolePack, owner: boolean): boolean {
  return owner && pack.visibility === "PRIVATE" && pack.is_mine && !pack.archived;
}

/** Archiving needs edit rights; built-in packs are never editable. */
export function canArchive(pack: RolePack): boolean {
  return pack.editable && !pack.archived;
}

/** The file name the API gives a pack's YAML export. */
export function packExportFilename(pack: Pick<RolePack, "slug" | "version">): string {
  return `${pack.slug}-v${pack.version}.yaml`;
}

// --- the editable spec model ---------------------------------------------------------------------------------------

const text = z.string().catch("");
const textList = z.array(z.string()).catch([]);
const seniorityList = z.array(z.enum(SENIORITY)).catch([]);

// Loose objects keep fields the form does not edit (probe ladders, practical tasks, structure, …) on the way back.
const seedQuestionSchema = z.looseObject({
  text,
  difficulty: z.number().int().min(1).max(5).catch(3),
  level: seniorityList,
  expected_signals: textList,
  follow_ups: textList,
});

const competencySchema = z.looseObject({
  id: text,
  name: text,
  description: text,
  anchors: z.record(z.string(), z.string()).catch({}),
  signals: textList,
  red_flags: textList,
  seed_questions: z.array(seedQuestionSchema).catch([]),
});

const roleSchema = z.looseObject({
  id: text,
  name: text,
  weights: z.record(z.string(), z.number()).catch({}),
});

const specSchema = z.looseObject({
  slug: text,
  name: text,
  description: text,
  domain_tags: textList,
  seniority_levels: z.array(z.enum(SENIORITY)).catch([...SENIORITY]),
  competencies: z.array(competencySchema).catch([]),
  roles: z.array(roleSchema).catch([]),
});

export type SeedQuestion = z.infer<typeof seedQuestionSchema>;
export type Competency = z.infer<typeof competencySchema>;
export type RoleVariant = z.infer<typeof roleSchema>;
export type PackSpec = z.infer<typeof specSchema>;

/** A spec object (from the API or a draft) as the form's model; null when it is not an object at all. */
export function parseSpec(raw: unknown): PackSpec | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const parsed = specSchema.safeParse(raw);
  return parsed.success ? parsed.data : null;
}

/** The anchor text of a level; JSON specs key anchors by the level as a string. */
export function anchorText(competency: Competency, level: number): string {
  return competency.anchors[String(level)] ?? "";
}

export function setAnchor(competency: Competency, level: number, value: string): Competency {
  return { ...competency, anchors: { ...competency.anchors, [String(level)]: value } };
}

export function updateCompetency(spec: PackSpec, index: number, competency: Competency): PackSpec {
  return { ...spec, competencies: spec.competencies.map((c, i) => (i === index ? competency : c)) };
}

export function updateRole(spec: PackSpec, index: number, role: RoleVariant): PackSpec {
  return { ...spec, roles: spec.roles.map((r, i) => (i === index ? role : r)) };
}

/** A unique id (`[a-z][a-z0-9_.-]{1,63}`) derived from a display name, e.g. "Board bring-up" → "board_bring-up". */
export function idFromName(name: string, taken: readonly string[]): string {
  const base =
    name
      .toLowerCase()
      .normalize("NFKD")
      .replace(/[^a-z0-9_.-]+/g, "_")
      .replace(/^[^a-z]+/, "")
      .replace(/_+$/, "")
      .slice(0, 56) || "item";
  const stem = base.length < 2 ? `${base}_x` : base;
  const used = new Set(taken);
  if (!used.has(stem)) return stem;
  for (let n = 2; ; n += 1) {
    const candidate = `${stem}_${n}`;
    if (!used.has(candidate)) return candidate;
  }
}

/** A new competency with empty anchors to fill in (validation lists what is still missing). */
export function addCompetency(spec: PackSpec, name: string): PackSpec {
  const id = idFromName(
    name,
    spec.competencies.map((c) => c.id),
  );
  const competency: Competency = {
    id,
    name: name.trim(),
    description: "",
    anchors: { "1": "", "2": "", "3": "", "4": "" },
    signals: [],
    red_flags: [],
    seed_questions: [],
  };
  return { ...spec, competencies: [...spec.competencies, competency] };
}

/** Remove a competency and its weight in every role variant. */
export function removeCompetency(spec: PackSpec, index: number): PackSpec {
  const id = spec.competencies[index]?.id;
  if (id === undefined) return spec;
  return {
    ...spec,
    competencies: spec.competencies.filter((_, i) => i !== index),
    roles: spec.roles.map((role) => {
      if (!(id in role.weights)) return role;
      const weights = { ...role.weights };
      delete weights[id];
      return { ...role, weights };
    }),
  };
}

/** A new role variant weighting every competency equally. */
export function addRole(spec: PackSpec, name: string): PackSpec {
  const id = idFromName(
    name,
    spec.roles.map((r) => r.id),
  );
  const even = Object.fromEntries(spec.competencies.map((c) => [c.id, 1]));
  return { ...spec, roles: [...spec.roles, { id, name: name.trim(), weights: normaliseWeights(even) }] };
}

export function removeRole(spec: PackSpec, index: number): PackSpec {
  return { ...spec, roles: spec.roles.filter((_, i) => i !== index) };
}

export function emptySeedQuestion(): SeedQuestion {
  return { text: "", difficulty: 3, level: [], expected_signals: [], follow_ups: [] };
}

/** Newline-separated text → list items (trimmed, blanks dropped). */
export function linesToList(value: string, separator: "\n" | "," = "\n"): string[] {
  return value
    .split(separator)
    .map((line) => line.trim())
    .filter(Boolean);
}

export function listToLines(list: readonly string[], separator: "\n" | "," = "\n"): string {
  return list.join(separator === "," ? ", " : "\n");
}

export function sameList(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((value, i) => value === b[i]);
}

// --- weights ------------------------------------------------------------------------------------------------------

export function weightTotal(weights: Readonly<Record<string, number>>): number {
  return Object.values(weights).reduce((sum, w) => sum + (Number.isFinite(w) ? w : 0), 0);
}

export function weightsBalanced(weights: Readonly<Record<string, number>>): boolean {
  return Math.abs(weightTotal(weights) - 1) <= WEIGHT_TOLERANCE + 1e-9;
}

/** A weight as a share with two decimals ("0.25"). */
export function formatWeight(weight: number): string {
  return (Math.round(weight * 100) / 100).toFixed(2);
}

/** Set one weight; zero, negative or empty removes the competency from the variant (weights must be positive). */
export function setWeight(
  weights: Readonly<Record<string, number>>,
  competencyId: string,
  value: number,
): Record<string, number> {
  const next = { ...weights };
  if (!Number.isFinite(value) || value <= 0) delete next[competencyId];
  else next[competencyId] = Math.round(value * 1000) / 1000;
  return next;
}

/**
 * Scale positive weights to sum to exactly 1.00 in hundredths (largest remainder), keeping their order. Weights
 * that would round to zero keep the minimum 0.01, since the API rejects zero weights.
 */
export function normaliseWeights(weights: Readonly<Record<string, number>>): Record<string, number> {
  const entries = Object.entries(weights).filter(([, w]) => Number.isFinite(w) && w > 0);
  const total = entries.reduce((sum, [, w]) => sum + w, 0);
  if (entries.length === 0 || total <= 0) return {};
  const raw = entries.map(([id, w]) => ({ id, exact: (w / total) * 100 }));
  const cents = raw.map((r) => ({ ...r, cents: Math.max(1, Math.floor(r.exact)) }));
  let left = 100 - cents.reduce((sum, c) => sum + c.cents, 0);
  const byRemainder = [...cents].sort((a, b) => b.exact - b.cents - (a.exact - a.cents) || b.exact - a.exact);
  for (let i = 0; left > 0; i = (i + 1) % byRemainder.length, left -= 1) byRemainder[i].cents += 1;
  // Only when more than 100 weights hit the 0.01 floor; a pack has at most 40 competencies.
  const byLargest = [...cents].sort((a, b) => b.cents - a.cents);
  for (const item of byLargest) {
    while (left < 0 && item.cents > 1) {
      item.cents -= 1;
      left += 1;
    }
  }
  return Object.fromEntries(cents.map((c) => [c.id, c.cents / 100]));
}

// --- YAML ---------------------------------------------------------------------------------------------------------

const RESERVED = /^(?:y|n|yes|no|true|false|on|off|null|~)$/i;
const PLAIN = /^[A-Za-z][A-Za-z0-9 _.,/()+'-]*$/;

function scalar(value: string, key = false): string {
  if (key && /^\d+$/.test(value)) return value;
  if (PLAIN.test(value) && value.trim() === value && !RESERVED.test(value)) return value;
  // A JSON string literal is a valid YAML double-quoted scalar.
  return JSON.stringify(value);
}

/** Tabs and control characters (other than newlines) are kept escaped in a quoted scalar instead. */
function hasControlCharacters(value: string): boolean {
  for (let i = 0; i < value.length; i += 1) {
    const code = value.charCodeAt(i);
    if ((code < 32 && code !== 10) || (code >= 0x7f && code <= 0x9f)) return true;
  }
  return false;
}

function blockLiteral(value: string, indent: number): string | null {
  if (!value.includes("\n") || value.includes("\r") || /^[ \t]/.test(value) || value.endsWith("\n\n")) return null;
  // Trailing spaces on a line are easy to lose in an editor; keep those strings quoted.
  if (hasControlCharacters(value) || / $/m.test(value)) return null;
  const chomp = value.endsWith("\n") ? "|" : "|-";
  const body = (value.endsWith("\n") ? value.slice(0, -1) : value)
    .split("\n")
    .map((line) => (line ? " ".repeat(indent) + line : ""))
    .join("\n");
  return `${chomp}\n${body}`;
}

function inline(value: unknown): string | null {
  if (value === null || value === undefined) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "null";
  if (typeof value === "string") return scalar(value);
  if (Array.isArray(value) && value.length === 0) return "[]";
  if (typeof value === "object" && Object.keys(value).length === 0) return "{}";
  return null;
}

function emit(value: unknown, indent: number): string[] {
  const pad = " ".repeat(indent);
  if (Array.isArray(value)) {
    return value.flatMap((item) => {
      if (typeof item === "string") {
        const block = blockLiteral(item, indent + 2);
        if (block) return [`${pad}- ${block}`];
      }
      const simple = inline(item);
      if (simple !== null) return [`${pad}- ${simple}`];
      const nested = emit(item, indent + 2);
      if (Array.isArray(item)) return [`${pad}-`, ...nested];
      return [`${pad}- ${nested[0].slice(indent + 2)}`, ...nested.slice(1)];
    });
  }
  if (typeof value === "object" && value !== null) {
    return Object.entries(value).flatMap(([key, item]) => {
      if (item === undefined) return [];
      const name = scalar(key, true);
      if (typeof item === "string") {
        const block = blockLiteral(item, indent + 2);
        if (block) return [`${pad}${name}: ${block}`];
      }
      const simple = inline(item);
      if (simple !== null) return [`${pad}${name}: ${simple}`];
      return [`${pad}${name}:`, ...emit(item, indent + 2)];
    });
  }
  return [`${pad}${inline(value) ?? "null"}`];
}

/** Serialise a spec (plain JSON data) as YAML for the YAML tab; the API parses it with a YAML 1.1 safe loader. */
export function toYaml(value: unknown): string {
  return `${emit(value, 0).join("\n")}\n`;
}

// --- validation problems ------------------------------------------------------------------------------------------

const problemListSchema = z.array(z.object({ path: z.string(), message: z.string() }));

/** The spec problems of a rejected save (a 422 problem carries them under `problems`), or none. */
export function problemsFromError(error: unknown): SpecProblem[] {
  const problem = isProblem(error)
    ? error
    : typeof error === "object" && error !== null && "problem" in error
      ? error.problem
      : null;
  if (!isProblem(problem)) return [];
  const parsed = problemListSchema.safeParse(problem.problems);
  return parsed.success ? parsed.data : [];
}

/** A problem path such as `competencies.0.anchors` with list positions shown 1-based and named from the spec. */
export function describePath(path: string, spec: PackSpec | null): string {
  const parts = path.split(".");
  const out: string[] = [];
  for (let i = 0; i < parts.length; i += 1) {
    const part = parts[i];
    const index = Number(parts[i + 1]);
    if ((part === "competencies" || part === "roles") && Number.isInteger(index)) {
      const item = part === "competencies" ? spec?.competencies[index] : spec?.roles[index];
      out.push(`${part === "competencies" ? "competency" : "role"} ${item?.name || index + 1}`);
      i += 1;
    } else if (/^\d+$/.test(part) && parts[i - 1] === "anchors") {
      out.push(`level ${part}`);
    } else if (/^\d+$/.test(part)) {
      out.push(`#${Number(part) + 1}`);
    } else {
      out.push(part.replace(/_/g, " "));
    }
  }
  return out.join(" › ");
}

// --- Role Builder diff --------------------------------------------------------------------------------------------

export interface WeightChangeRow {
  role: string;
  competency: string;
  before: number | null;
  after: number | null;
}

export function weightChangeRows(diff: PackDiff): WeightChangeRow[] {
  return Object.entries(diff.weight_changes).flatMap(([role, changes]) =>
    Object.entries(changes).map(([competency, values]) => ({
      role,
      competency,
      before: values[0] ?? null,
      after: values[1] ?? null,
    })),
  );
}

export function diffIsEmpty(diff: PackDiff): boolean {
  return (
    diff.added_competencies.length === 0 &&
    diff.removed_competencies.length === 0 &&
    diff.changed_competencies.length === 0 &&
    diff.added_roles.length === 0 &&
    diff.removed_roles.length === 0 &&
    weightChangeRows(diff).length === 0
  );
}

/**
 * The YAML to open a Role Builder draft with: the API's YAML when the draft is valid, otherwise the raw spec as
 * JSON (which is valid YAML), so an invalid draft can still be fixed in the editor.
 */
export function draftYaml(result: Pick<BuildOut, "yaml" | "spec">): string {
  return result.yaml ?? `${JSON.stringify(result.spec, null, 2)}\n`;
}

// --- editor draft hand-off (Role Builder → new pack editor) ----------------------------------------------------------

/** sessionStorage key of the draft the new-pack editor opens. */
export const DRAFT_STORAGE_KEY = "intrv.rolepack.draft";

const storedDraftSchema = z.object({ yaml: z.string().min(1).max(500_000) });

export function encodeDraft(yaml: string): string {
  return JSON.stringify({ yaml });
}

export function decodeDraft(stored: string | null): string | null {
  if (!stored) return null;
  try {
    const parsed = storedDraftSchema.safeParse(JSON.parse(stored));
    return parsed.success ? parsed.data.yaml : null;
  } catch {
    return null;
  }
}
