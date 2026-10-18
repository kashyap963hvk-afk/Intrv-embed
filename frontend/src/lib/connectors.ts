/** Pure helpers for the "+" connectors: tabs, selections, source and account bodies, OAuth messages and sync results. */

import { z } from "zod";

import type { Schemas } from "@/lib/api/client";
import type { DocTypeChoice } from "@/lib/knowledge";

export type ConnectorKind = Schemas["ConnectorKind"];
export type ConnectorKindInfo = Schemas["ConnectorKindOut"];
export type ConnectorAccount = Schemas["AccountOut"];
export type RemoteItem = Schemas["RemoteItemOut"];
export type SelectedItem = Schemas["SelectedItem"];
export type SourceIn = Schemas["SourceIn"];
export type Source = Schemas["SourceOut"];
export type SourceStatus = Schemas["SourceStatus"];
export type S3AccountIn = Schemas["S3AccountIn"];
export type AzureAccountIn = Schemas["AzureAccountIn"];
export type CredentialAccountIn = S3AccountIn | AzureAccountIn;
export type TestResult = Schemas["TestResult"];

// --- kinds and tabs ---------------------------------------------------------------------------------------------

/** The connector tabs of the "+" dialog, in display order, with their short tab labels. URL lives in the Link tab. */
export const CONNECTOR_TABS: readonly { kind: ConnectorKind; tab: string }[] = [
  { kind: "GOOGLE_DRIVE", tab: "Google Drive" },
  { kind: "GOOGLE_DRIVE_ORG", tab: "Org Drive" },
  { kind: "S3_COMPATIBLE", tab: "S3 / Cloud bucket" },
  { kind: "MS_GRAPH", tab: "OneDrive / SharePoint" },
  { kind: "AZURE_BLOB", tab: "Azure" },
  { kind: "DROPBOX", tab: "Dropbox" },
];

const KIND_LABEL: Record<ConnectorKind, string> = {
  GOOGLE_DRIVE: "Google Drive",
  GOOGLE_DRIVE_ORG: "Google Drive (organization)",
  S3_COMPATIBLE: "S3 / cloud bucket",
  AZURE_BLOB: "Azure Blob Storage",
  MS_GRAPH: "OneDrive / SharePoint",
  DROPBOX: "Dropbox",
  URL: "Web page",
};

export function kindLabel(kind: ConnectorKind): string {
  return KIND_LABEL[kind];
}

/** The connector tabs this user sees: owners-only kinds (organization Drive) are hidden from members. */
export function connectorTabs(
  kinds: readonly ConnectorKindInfo[],
  owner: boolean,
): { info: ConnectorKindInfo; tab: string }[] {
  const byKind = new Map(kinds.map((k) => [k.kind, k]));
  const tabs: { info: ConnectorKindInfo; tab: string }[] = [];
  for (const { kind, tab } of CONNECTOR_TABS) {
    const info = byKind.get(kind);
    if (info && (!info.owners_only || owner)) tabs.push({ info, tab });
  }
  return tabs;
}

export type OAuthProvider = "google" | "microsoft" | "dropbox";

const OAUTH_PROVIDERS: Partial<Record<ConnectorKind, OAuthProvider>> = {
  GOOGLE_DRIVE: "google",
  MS_GRAPH: "microsoft",
  DROPBOX: "dropbox",
};

/** The OAuth provider that connects accounts of a kind, or null for kinds that use credentials. */
export function oauthProvider(kind: ConnectorKind): OAuthProvider | null {
  return OAUTH_PROVIDERS[kind] ?? null;
}

const PROVIDER_LABEL: Record<OAuthProvider, string> = {
  google: "Google Drive",
  microsoft: "OneDrive / SharePoint",
  dropbox: "Dropbox",
};

export function providerLabel(provider: string): string {
  return provider in PROVIDER_LABEL ? PROVIDER_LABEL[provider as OAuthProvider] : provider;
}

export function accountsOfKind(accounts: readonly ConnectorAccount[], kind: ConnectorKind): ConnectorAccount[] {
  return accounts.filter((a) => a.kind === kind);
}

function configString(account: ConnectorAccount, key: string): string {
  const value = account.config[key];
  return typeof value === "string" ? value : "";
}

/** Who an OAuth account is signed in as, or the bucket/container a credentials account reads. */
export function accountDetail(account: ConnectorAccount): string {
  const connectedAs = configString(account, "connected_as");
  if (connectedAs) return connectedAs;
  const where = configString(account, "bucket") || configString(account, "container");
  const root = configString(account, "root_prefix");
  return root && where ? `${where}/${root}` : where;
}

// --- browsing and selection -------------------------------------------------------------------------------------

export interface Crumb {
  /** The folder id passed to browse as `path`; null for the top level. */
  id: string | null;
  name: string;
}

export const ROOT_CRUMB: Crumb = { id: null, name: "Top" };

export function enterFolder(trail: readonly Crumb[], folder: RemoteItem): Crumb[] {
  return [...trail, { id: folder.id, name: folder.name || folder.id }];
}

/** Go back to the crumb at `index` (inclusive). */
export function trailTo(trail: readonly Crumb[], index: number): Crumb[] {
  return trail.slice(0, Math.max(1, index + 1));
}

export function toSelectedItem(item: RemoteItem): SelectedItem {
  return { id: item.id, name: item.name, path: item.path, is_folder: item.is_folder };
}

/** Add the item when it is not selected yet, otherwise remove it. */
export function toggleItem(selection: readonly SelectedItem[], item: SelectedItem): SelectedItem[] {
  return selection.some((s) => s.id === item.id) ? selection.filter((s) => s.id !== item.id) : [...selection, item];
}

/** Add items (from the Google Picker, say) to a selection, skipping ids already in it. */
export function mergeItems(selection: readonly SelectedItem[], added: readonly SelectedItem[]): SelectedItem[] {
  const seen = new Set(selection.map((s) => s.id));
  const merged = [...selection];
  for (const item of added) {
    if (seen.has(item.id)) continue;
    seen.add(item.id);
    merged.push(item);
  }
  return merged;
}

/**
 * The prefix that imports everything in the folder being browsed (S3 and Azure): the folder's id, which already ends
 * in "/", or at the top level the account's root prefix.
 */
export function folderPrefix(account: ConnectorAccount, folderId: string | null): string {
  if (folderId !== null) return folderId;
  const root = configString(account, "root_prefix").replace(/^\/+|\/+$/g, "");
  return root ? `${root}/` : "";
}

const MAX_SOURCE_NAME = 200;
/** The API accepts at most this many chosen items per source; choose their folder instead. */
export const MAX_SOURCE_ITEMS = 200;

function lastSegment(path: string): string {
  return path.replace(/\/+$/, "").split("/").pop() ?? "";
}

/** A name for a new source from what was chosen; the user can type their own instead. */
export function suggestSourceName(items: readonly SelectedItem[], prefix: string | null, fallback: string): string {
  let name: string;
  if (items.length === 0) {
    name = (prefix !== null && lastSegment(prefix)) || fallback;
  } else {
    const first = items[0].name || lastSegment(items[0].path ?? "") || items[0].id;
    const more = items.length - 1 + (prefix !== null ? 1 : 0);
    name = more > 0 ? `${first} and ${more} more` : first;
  }
  return name.slice(0, MAX_SOURCE_NAME);
}

export interface SourceDraft {
  kind: ConnectorKind;
  accountId: string;
  name: string;
  items: readonly SelectedItem[];
  prefix: string | null;
  docType: DocTypeChoice;
  syncEnabled: boolean;
  intervalMinutes: number;
}

/** The body of POST /api/spaces/{space_id}/sources for a selection from a connected account. */
export function buildSourceIn(draft: SourceDraft): SourceIn {
  return {
    kind: draft.kind,
    account_id: draft.accountId,
    name: draft.name.trim().slice(0, MAX_SOURCE_NAME),
    items: draft.items.map((i) => ({ id: i.id, name: i.name ?? "", path: i.path ?? "", is_folder: i.is_folder })),
    prefix: draft.prefix,
    doc_type: draft.docType,
    sync_enabled: draft.syncEnabled,
    interval_minutes: clampInterval(draft.intervalMinutes),
  };
}

/** A readable name for a URL source: host and path without the scheme. */
export function urlSourceName(url: string): string {
  try {
    const parsed = new URL(url.trim());
    const path = parsed.pathname === "/" ? "" : parsed.pathname.replace(/\/+$/, "");
    return `${parsed.host}${path}`.slice(0, MAX_SOURCE_NAME);
  } catch {
    return url.trim().slice(0, MAX_SOURCE_NAME);
  }
}

/** The body that keeps a web page in sync (a URL source) instead of fetching it once. */
export function buildUrlSourceIn(input: {
  url: string;
  title: string;
  docType: DocTypeChoice;
  intervalMinutes: number;
}): SourceIn {
  const url = input.url.trim();
  return {
    kind: "URL",
    name: input.title.trim().slice(0, MAX_SOURCE_NAME) || urlSourceName(url),
    url,
    items: [],
    doc_type: input.docType,
    sync_enabled: true,
    interval_minutes: clampInterval(input.intervalMinutes),
  };
}

// --- Google Picker ----------------------------------------------------------------------------------------------

export const DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder";

export interface PickedDocument {
  id: string;
  name?: string;
  mimeType?: string;
}

export function pickedToItems(docs: readonly PickedDocument[]): SelectedItem[] {
  return docs.map((d) => ({
    id: d.id,
    name: d.name ?? "",
    path: d.name ?? "",
    is_folder: d.mimeType === DRIVE_FOLDER_MIME,
  }));
}

// --- sync intervals ---------------------------------------------------------------------------------------------

export const MIN_INTERVAL_MINUTES = 15;
export const MAX_INTERVAL_MINUTES = 10_080;
export const DEFAULT_INTERVAL_MINUTES = 360;

export const INTERVAL_PRESETS: readonly { minutes: number; label: string }[] = [
  { minutes: 15, label: "Every 15 minutes" },
  { minutes: 60, label: "Every hour" },
  { minutes: 360, label: "Every 6 hours" },
  { minutes: 1440, label: "Daily" },
  { minutes: 10_080, label: "Weekly" },
];

export function clampInterval(minutes: number): number {
  if (!Number.isFinite(minutes)) return DEFAULT_INTERVAL_MINUTES;
  return Math.min(MAX_INTERVAL_MINUTES, Math.max(MIN_INTERVAL_MINUTES, Math.round(minutes)));
}

export function intervalLabel(minutes: number): string {
  const preset = INTERVAL_PRESETS.find((p) => p.minutes === minutes);
  if (preset) return preset.label;
  if (minutes % 1440 === 0) return `Every ${minutes / 1440} days`;
  if (minutes % 60 === 0) return `Every ${minutes / 60} hours`;
  return `Every ${minutes} minutes`;
}

/** The interval choices for a select: the presets, plus the current value when it is not one of them. */
export function intervalOptions(current: number): { minutes: number; label: string }[] {
  const options = [...INTERVAL_PRESETS];
  if (!options.some((o) => o.minutes === current)) options.push({ minutes: current, label: intervalLabel(current) });
  return options.sort((a, b) => a.minutes - b.minutes);
}

/** A `<select>` value as an interval in minutes, within the allowed range. */
export function toInterval(value: string): number {
  return clampInterval(Number.parseInt(value, 10));
}

// --- credentials forms ------------------------------------------------------------------------------------------

export interface S3Form {
  name: string;
  endpointUrl: string;
  region: string;
  bucket: string;
  rootPrefix: string;
  forcePathStyle: boolean;
  accessKey: string;
  secretKey: string;
  orgLevel: boolean;
}

export const EMPTY_S3_FORM: S3Form = {
  name: "",
  endpointUrl: "",
  region: "us-east-1",
  bucket: "",
  rootPrefix: "",
  forcePathStyle: true,
  accessKey: "",
  secretKey: "",
  orgLevel: false,
};

export function s3FormReady(form: S3Form): boolean {
  const keys = Boolean(form.accessKey.trim()) === Boolean(form.secretKey.trim());
  return form.bucket.trim().length >= 3 && form.region.trim().length > 0 && keys;
}

export function buildS3AccountIn(form: S3Form): S3AccountIn {
  const bucket = form.bucket.trim();
  const accessKey = form.accessKey.trim();
  return {
    kind: "S3_COMPATIBLE",
    name: form.name.trim() || bucket,
    endpoint_url: form.endpointUrl.trim() || null,
    region: form.region.trim() || "us-east-1",
    bucket,
    root_prefix: form.rootPrefix.trim().replace(/^\/+|\/+$/g, ""),
    force_path_style: form.forcePathStyle,
    access_key: accessKey || null,
    secret_key: accessKey ? form.secretKey.trim() : null,
    org_level: form.orgLevel,
  };
}

export type AzureAuthMode = "connection_string" | "sas_url";

export interface AzureForm {
  name: string;
  mode: AzureAuthMode;
  connectionString: string;
  container: string;
  sasUrl: string;
  rootPrefix: string;
  orgLevel: boolean;
}

export const EMPTY_AZURE_FORM: AzureForm = {
  name: "",
  mode: "connection_string",
  connectionString: "",
  container: "",
  sasUrl: "",
  rootPrefix: "",
  orgLevel: false,
};

export function azureFormReady(form: AzureForm): boolean {
  if (form.mode === "sas_url") return /^https:\/\//i.test(form.sasUrl.trim());
  return form.connectionString.trim().length > 0 && form.container.trim().length > 0;
}

/** Container name from a container SAS URL (`https://account.blob.core.windows.net/<container>?sv=…`). */
function sasContainer(url: string): string {
  try {
    return new URL(url).pathname.split("/").filter(Boolean)[0] ?? "";
  } catch {
    return "";
  }
}

export function buildAzureAccountIn(form: AzureForm): AzureAccountIn {
  const sas = form.mode === "sas_url";
  const container = sas ? sasContainer(form.sasUrl.trim()) : form.container.trim();
  return {
    kind: "AZURE_BLOB",
    name: form.name.trim() || container || "Azure Blob Storage",
    container: sas ? null : container,
    root_prefix: form.rootPrefix.trim().replace(/^\/+|\/+$/g, ""),
    connection_string: sas ? null : form.connectionString.trim(),
    container_sas_url: sas ? form.sasUrl.trim() : null,
    org_level: form.orgLevel,
  };
}

// --- organization Google Drive ----------------------------------------------------------------------------------

const FOLDER_ID = /^[A-Za-z0-9_-]{5,200}$/;
const MAX_ROOT_FOLDERS = 20;

/** A folder id from a line: a bare id, or a Drive folder link (`…/folders/<id>` or `…?id=<id>`). */
function folderIdFrom(line: string): string {
  const fromPath = /\/folders\/([A-Za-z0-9_-]+)/.exec(line);
  if (fromPath) return fromPath[1];
  const fromQuery = /[?&]id=([A-Za-z0-9_-]+)/.exec(line);
  if (fromQuery) return fromQuery[1];
  return line;
}

export type FolderIdsResult = { ok: true; ids: string[] } | { ok: false; error: string };

/** Parse the shared-folder textarea: one folder id or link per line, blank lines ignored, duplicates dropped. */
export function parseFolderIds(text: string): FolderIdsResult {
  const ids: string[] = [];
  for (const [index, raw] of text.split("\n").entries()) {
    const line = raw.trim();
    if (!line) continue;
    const id = folderIdFrom(line);
    if (!FOLDER_ID.test(id)) return { ok: false, error: `Line ${index + 1}: not a Google Drive folder id or link.` };
    if (!ids.includes(id)) ids.push(id);
  }
  if (ids.length === 0) return { ok: false, error: "Enter at least one folder id." };
  if (ids.length > MAX_ROOT_FOLDERS) return { ok: false, error: `At most ${MAX_ROOT_FOLDERS} folders.` };
  return { ok: true, ids };
}

// --- OAuth popup ------------------------------------------------------------------------------------------------

export const OAUTH_MESSAGE_TYPE = "intrv:oauth";
/** Same-origin fallback channel: some providers' pages sever `window.opener` (Cross-Origin-Opener-Policy). */
export const OAUTH_CHANNEL = "intrv-oauth";

export interface OAuthResult {
  status: "ok" | "error";
  provider: string;
  detail: string;
}

export interface OAuthMessage extends OAuthResult {
  type: typeof OAUTH_MESSAGE_TYPE;
}

/** The outcome the backend put in the query string of `/app/knowledge/connected`. */
export function oauthResultFromParams(params: { get(name: string): string | null }): OAuthResult {
  const detail = (params.get("detail") ?? "").slice(0, 300);
  return {
    status: params.get("status") === "ok" ? "ok" : "error",
    provider: (params.get("provider") ?? "").slice(0, 40),
    detail,
  };
}

export function oauthMessage(result: OAuthResult): OAuthMessage {
  return { type: OAUTH_MESSAGE_TYPE, ...result };
}

const oauthMessageSchema = z.object({
  type: z.literal(OAUTH_MESSAGE_TYPE),
  status: z.enum(["ok", "error"]),
  provider: z.string().max(40),
  detail: z.string().max(300),
});

/** The result carried by a message from the OAuth popup; anything else is ignored (null). */
export function parseOAuthMessage(data: unknown): OAuthResult | null {
  const parsed = oauthMessageSchema.safeParse(data);
  if (!parsed.success) return null;
  const { status, provider, detail } = parsed.data;
  return { status, provider, detail };
}

// --- sources ----------------------------------------------------------------------------------------------------

export function canManageSource(source: Source, owner: boolean): boolean {
  return source.created_by_me || owner;
}

const syncResultSchema = z.object({
  added: z.number().int().nonnegative().catch(0),
  updated: z.number().int().nonnegative().catch(0),
  unchanged: z.number().int().nonnegative().catch(0),
  deleted: z.number().int().nonnegative().catch(0),
  skipped: z.array(z.string()).catch([]),
  failed: z.array(z.string()).catch([]),
  truncated: z.boolean().catch(false),
});

export type SyncResult = z.infer<typeof syncResultSchema>;

/** The outcome of a source's last run, or null before its first run finishes. */
export function parseSyncResult(raw: Record<string, unknown>): SyncResult | null {
  if (Object.keys(raw).length === 0) return null;
  const parsed = syncResultSchema.safeParse(raw);
  return parsed.success ? parsed.data : null;
}

/** One line describing a sync run, e.g. "3 added, 1 updated, 12 unchanged · 2 skipped". */
export function syncResultSummary(result: SyncResult): string {
  const changes = (
    [
      [result.added, "added"],
      [result.updated, "updated"],
      [result.deleted, "removed"],
      [result.unchanged, "unchanged"],
    ] as const
  )
    .filter(([count]) => count > 0)
    .map(([count, label]) => `${count} ${label}`);
  const parts = [changes.length > 0 ? changes.join(", ") : "No files found"];
  if (result.skipped.length > 0) parts.push(`${result.skipped.length} skipped`);
  if (result.failed.length > 0) parts.push(`${result.failed.length} failed`);
  if (result.truncated) parts.push("more files remain; the next sync continues");
  return parts.join(" · ");
}

const RELATIVE_UNITS: readonly [Intl.RelativeTimeFormatUnit, number][] = [
  ["day", 86_400_000],
  ["hour", 3_600_000],
  ["minute", 60_000],
];

/** "5 minutes ago", "in 2 hours", "yesterday"; "just now" within a minute. Null for a missing or invalid time. */
export function formatRelative(iso: string | null, now: number): string | null {
  if (!iso) return null;
  const at = new Date(iso).getTime();
  if (Number.isNaN(at)) return null;
  const diff = at - now;
  if (Math.abs(diff) < 60_000) return diff > 0 ? "in a moment" : "just now";
  const format = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  for (const [unit, ms] of RELATIVE_UNITS) {
    if (Math.abs(diff) >= ms || unit === "minute") return format.format(Math.round(diff / ms), unit);
  }
  return null;
}
