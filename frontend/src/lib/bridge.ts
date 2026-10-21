/**
 * Pure helpers for the Local Model Bridge (ARCHITECTURE §10, ADR 0008): parsing what a paired PC reports, the
 * pairing countdown and command, honest hardware notes, and the profile bodies for BRIDGE and GGUF_MANAGED models.
 */

import { z } from "zod";

import type { Schemas } from "@/lib/api/client";

export type BridgeDevice = Schemas["DeviceOut"];
export type PairCode = Schemas["PairOut"];
export type ProfileCreate = Schemas["ProfileCreate"];
export type LlmProfile = Schemas["ProfileOut"];
export type QualityTier = Schemas["QualityTier"];

// --- what the PC reports ---------------------------------------------------------------------------------------

const optionalNumber = z.number().nonnegative().nullable().catch(null);
const optionalText = z.string().min(1).nullable().catch(null);

const bridgeModelSchema = z.object({
  id: z.string().min(1),
  name: z.string().catch(""),
  source: z.string().catch(""),
  size_bytes: optionalNumber,
  quant: optionalText,
  context_length: optionalNumber,
  arch: optionalText,
});

export type BridgeModel = z.infer<typeof bridgeModelSchema>;

/** The models a device announced (entries without an id are dropped; a missing name falls back to the id). */
export function parseBridgeModels(raw: readonly unknown[]): BridgeModel[] {
  const models: BridgeModel[] = [];
  for (const entry of raw) {
    const parsed = bridgeModelSchema.safeParse(entry);
    if (parsed.success) models.push({ ...parsed.data, name: parsed.data.name.trim() || parsed.data.id });
  }
  return models;
}

/** The bridge app version the device reported when it connected. */
export function deviceVersion(device: Pick<BridgeDevice, "capabilities">): string | null {
  const version = device.capabilities.version;
  return typeof version === "string" && version.trim() ? version.trim() : null;
}

const SOURCE_LABEL: Record<string, string> = {
  gguf: "GGUF file",
  ollama: "Ollama",
  lmstudio: "LM Studio",
  vllm: "vLLM",
};

export function sourceLabel(source: string): string {
  return SOURCE_LABEL[source] ?? (source || "local");
}

export function formatBytes(bytes: number | null): string | null {
  if (bytes === null || !Number.isFinite(bytes) || bytes <= 0) return null;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000;
    unit += 1;
  }
  return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

// --- pairing ---------------------------------------------------------------------------------------------------

export const PAIR_CODE_PATTERN = /^\d{6}$/;

/** When a pairing code stops working, from the moment it was created. */
export function pairingDeadline(createdAt: number, expiresInSeconds: number): number {
  return createdAt + Math.max(0, expiresInSeconds) * 1000;
}

/** Seconds until the deadline, never more than `total` (the clock tick driving `now` may lag a little). */
export function secondsLeft(deadline: number, now: number, total = Number.POSITIVE_INFINITY): number {
  return Math.min(total, Math.max(0, Math.ceil((deadline - now) / 1000)));
}

/** "9:05" */
export function formatCountdown(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** A device name that is safe to paste inside double quotes in any shell (letters, digits, space, . _ -). */
export function safeDeviceName(name: string): string {
  const cleaned = name
    .replace(/[^A-Za-z0-9 ._-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 60);
  return cleaned || "My PC";
}

/** The command the member runs on their PC to claim the code. */
export function pairCommand(server: string, code: string, deviceName: string): string {
  return `intrv-bridge pair ${server.replace(/\/+$/, "")} ${code} --name "${safeDeviceName(deviceName)}"`;
}

/** Devices that were not there when the pairing code was created. */
export function newDevices(known: ReadonlySet<string>, devices: readonly BridgeDevice[]): BridgeDevice[] {
  return devices.filter((d) => !known.has(d.id));
}

// --- honest hardware notes (ARCHITECTURE §10) ------------------------------------------------------------------

/** Same rule as the server's capability probe: the largest "<n>b" in the name, in billions of parameters. */
const SIZE_PATTERN = /(?<![\d.])(\d+(?:\.\d+)?)\s*b(?![a-z])/gi;

export function parameterBillions(modelName: string): number | null {
  const sizes = [...modelName.matchAll(SIZE_PATTERN)].map((m) => Number.parseFloat(m[1]));
  const valid = sizes.filter((n) => Number.isFinite(n) && n > 0);
  return valid.length ? Math.max(...valid) : null;
}

/** The best rating a model of this size can get from the probe (the probe itself decides the real one). */
export function tierCeiling(billions: number | null): QualityTier | null {
  if (billions === null) return null;
  if (billions < 10) return "BASIC";
  if (billions < 20) return "USABLE";
  if (billions < 50) return "GOOD";
  return "EXCELLENT";
}

export const TIER_LABEL: Record<QualityTier, string> = {
  EXCELLENT: "Excellent",
  GOOD: "Good",
  USABLE: "Usable",
  BASIC: "Basic",
};

const TIER_USE: Record<QualityTier, string> = {
  BASIC: "CV extraction and turn control only; not as the interviewer or grader",
  USABLE: "drafting and extraction; weak as the interviewer or grader",
  GOOD: "a capable interviewer and grader",
  EXCELLENT: "any role, including the interviewer and grader",
};

/** Rough read speeds: USB 2.0 about 30 MB/s, USB 3 about 120–240 MB/s. */
const USB2_BYTES_PER_S = 30e6;
const USB3_FAST_BYTES_PER_S = 240e6;
const USB3_SLOW_BYTES_PER_S = 120e6;

export function formatSeconds(seconds: number): string {
  if (seconds < 90) return `${Math.max(1, Math.round(seconds))} s`;
  return `${(seconds / 60).toFixed(1)} min`;
}

/** Plain-language notes about what to expect from a model on the member's own hardware. */
export function hardwareNotes(model: Pick<BridgeModel, "id" | "name" | "source" | "size_bytes">): string[] {
  const notes: string[] = [];
  const ceiling = tierCeiling(parameterBillions(`${model.name} ${model.id}`));
  if (ceiling) {
    notes.push(`A model of this size rates at most ${TIER_LABEL[ceiling]}: ${TIER_USE[ceiling]}.`);
  } else {
    notes.push("The name does not say the model's size; the test below rates it.");
  }
  const size = model.size_bytes;
  if (model.source === "gguf" && size) {
    const usb2 = size / USB2_BYTES_PER_S;
    const fast = size / USB3_FAST_BYTES_PER_S;
    const slow = size / USB3_SLOW_BYTES_PER_S;
    notes.push(
      `If the file is on a pendrive it loads fully into RAM first: about ${formatSeconds(usb2)} over USB 2.0, ` +
        `${formatSeconds(fast)}–${formatSeconds(slow)} over USB 3.`,
    );
  }
  notes.push(
    "On a CPU only, a 7B model writes roughly 5–15 tokens per second: fine for text interviews, slow for voice. " +
      "A GPU with 8 GB or more of VRAM changes that.",
  );
  return notes;
}

/** "850 ms", "2.4 s" */
export function formatMs(ms: number): string {
  return ms < 1000 ? `${Math.max(0, Math.round(ms))} ms` : `${(ms / 1000).toFixed(1)} s`;
}

// --- probe results ---------------------------------------------------------------------------------------------

const flag = z.boolean().catch(false);

const probeSchema = z.object({
  stream: flag,
  json_schema: flag,
  json_object: flag,
  tools: flag,
  vision: flag,
  ttft_ms: z.number().nullable().catch(null),
  tokens_per_s: z.number().nullable().catch(null),
  quality_score: z.number().nullable().catch(null),
  checks: z.record(z.string(), z.boolean()).catch({}),
  warnings: z.array(z.string()).catch([]),
});

export type ProbeSummary = z.infer<typeof probeSchema>;

/** A profile's stored capability probe (missing parts read as "not shown"). */
export function parseProbe(capabilities: Record<string, unknown>): ProbeSummary {
  return probeSchema.parse(capabilities);
}

const CHECK_LABEL: Record<string, string> = {
  follows_simple_instruction: "Follows a simple instruction",
  json_fidelity: "Returns valid JSON",
  embedded_c_fact: "Knows a basic embedded-C fact",
  keeps_scores_secret: "Keeps scores secret",
  asks_one_question: "Asks one question at a time",
};

export function checkLabel(key: string): string {
  return CHECK_LABEL[key] ?? key.replace(/_/g, " ");
}

/** True when the probe found no native JSON mode: structured steps rely on prompting and repair. */
export function weakJson(probe: ProbeSummary): boolean {
  return !probe.json_schema && !probe.json_object;
}

// --- profiles --------------------------------------------------------------------------------------------------

const MAX_PROFILE_NAME = 120;

export function bridgeProfileName(device: Pick<BridgeDevice, "name">, model: Pick<BridgeModel, "name">): string {
  return `${model.name} on ${device.name}`.slice(0, MAX_PROFILE_NAME);
}

/** The BRIDGE profile for one model on one paired PC (always personal: it depends on one person's PC). */
export function buildBridgeProfileIn(
  device: Pick<BridgeDevice, "id" | "name">,
  model: Pick<BridgeModel, "id" | "name">,
): ProfileCreate {
  return {
    name: bridgeProfileName(device, model),
    scope: "personal",
    kind: "BRIDGE",
    model: model.id,
    device_id: device.id,
  };
}

/** The member's existing profile for this model on this device, if any. */
export function profileForModel(
  profiles: readonly LlmProfile[],
  deviceId: string,
  modelId: string,
): LlmProfile | undefined {
  return profiles.find((p) => p.kind === "BRIDGE" && p.params.device_id === deviceId && p.model === modelId);
}

/** Profiles that point at a device (they stop working when the device is revoked). */
export function profilesForDevice(profiles: readonly LlmProfile[], deviceId: string): LlmProfile[] {
  return profiles.filter((p) => p.kind === "BRIDGE" && p.params.device_id === deviceId);
}

export interface GgufForm {
  name: string;
  path: string;
  shareWithOrg: boolean;
}

export const EMPTY_GGUF_FORM: GgufForm = { name: "", path: "", shareWithOrg: false };

function isAbsolutePath(path: string): boolean {
  return path.startsWith("/") || /^[A-Za-z]:[\\/]/.test(path);
}

/** The file name without folders or the .gguf extension. */
export function ggufModelName(path: string): string {
  const base = path.trim().split(/[\\/]/).pop() ?? "";
  return base.replace(/\.gguf$/i, "");
}

/** Why the server-side GGUF form cannot be sent, or null. The server still checks the allowed folders. */
export function ggufFormProblem(form: GgufForm): string | null {
  const path = form.path.trim();
  if (!path) return "Give the full path of the .gguf file on the server.";
  if (!isAbsolutePath(path)) return "The path must be absolute (start with / on Linux).";
  if (!/\.gguf$/i.test(path)) return "Only .gguf model files can be used.";
  if (path.length > 1000) return "The path is too long.";
  if (form.name.trim().length > MAX_PROFILE_NAME) return `The name is limited to ${MAX_PROFILE_NAME} characters.`;
  return null;
}

export function buildGgufProfileIn(form: GgufForm): ProfileCreate {
  const path = form.path.trim();
  const model = ggufModelName(path) || "local-gguf";
  return {
    name: form.name.trim() || model.slice(0, MAX_PROFILE_NAME),
    scope: form.shareWithOrg ? "org" : "personal",
    kind: "GGUF_MANAGED",
    model: model.slice(0, 200),
    model_path: path,
  };
}
