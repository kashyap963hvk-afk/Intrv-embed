/**
 * Pure helpers for the voice room (ADR 0009): the voice service's connection details, microphone constraints and
 * level, engine frames relayed as RTVI server messages, live captions from the bot's spoken output, and messages
 * for what went wrong.
 */

import { z } from "zod";

import { parseServerFrameData, type ServerFrame } from "@/lib/interviews";

const iceServerSchema = z.object({
  urls: z.union([z.string().min(1), z.array(z.string().min(1)).min(1)]),
  username: z.string().optional(),
  credential: z.string().optional(),
});

const voiceInfoSchema = z.object({
  available: z.boolean().catch(false),
  offer_url: z
    .url({ protocol: /^https?$/ })
    .nullable()
    .catch(null),
  ice_servers: z.array(iceServerSchema).catch([]),
});

export interface VoiceInfo {
  /** Voice can be used: the service is configured and has an offer URL. */
  available: boolean;
  offerUrl: string | null;
  iceServers: RTCIceServer[];
}

export function parseVoiceInfo(raw: unknown): VoiceInfo {
  const parsed = voiceInfoSchema.safeParse(raw);
  if (!parsed.success) return { available: false, offerUrl: null, iceServers: [] };
  const { available, offer_url, ice_servers } = parsed.data;
  return {
    available: available && offer_url !== null,
    offerUrl: offer_url,
    iceServers: ice_servers.map((server) => ({
      urls: server.urls,
      ...(server.username !== undefined ? { username: server.username } : {}),
      ...(server.credential !== undefined ? { credential: server.credential } : {}),
    })),
  };
}

/** How long a voice connection may take before the room offers text instead. */
export const VOICE_CONNECT_TIMEOUT_MS = 20_000;

/** The browser's echo cancellation, noise suppression and gain control stay on (ARCHITECTURE §14.1). */
export function micConstraints(deviceId: string | null): MediaStreamConstraints {
  return {
    audio: {
      ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
    video: false,
  };
}

/** Microphone level from analyser time-domain bytes (128 = silence): RMS, scaled so speech fills the meter. */
export function micLevel(samples: Uint8Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (const value of samples) {
    const centred = (value - 128) / 128;
    sum += centred * centred;
  }
  return Math.min(1, Math.sqrt(sum / samples.length) * 4);
}

/** A device's label for the picker (labels are empty until permission is granted). */
export function deviceLabel(device: Pick<MediaDeviceInfo, "label" | "deviceId">, index: number): string {
  return device.label || (device.deviceId === "default" ? "Default microphone" : `Microphone ${index + 1}`);
}

// --- frames relayed by the voice service --------------------------------------------------------------------------

/** An engine frame (state, accepted, transcript, error, ended, turn.end) from an RTVI server message, or null. */
export function relayedFrame(data: unknown): ServerFrame | null {
  return parseServerFrameData(data);
}

// --- live captions ------------------------------------------------------------------------------------------------

/** The fields of an RTVI `bot-output` message the captions use. */
export interface BotOutput {
  text: string;
  text_type?: string;
  segment_id?: number;
  spoken?: boolean;
  will_be_spoken?: boolean;
  spoken_status?: string;
  spoken_progress?: { accumulated_text: string; remaining_text: string };
}

export interface CaptionSegment {
  id: string;
  text: string;
}

export const CAPTION_SEGMENTS = 2;

/**
 * Fold one bot-output message into the captions: text announced before it is spoken is skipped, text being spoken
 * shows as far as it has been spoken, and the last two segments stay on screen.
 */
export function captionStep(segments: readonly CaptionSegment[], output: BotOutput): CaptionSegment[] {
  const announced = output.will_be_spoken === true && output.spoken_status === "new" && output.spoken !== true;
  if (announced) return [...segments];
  const textNow =
    output.spoken_status === "in-progress" && output.spoken_progress
      ? output.spoken_progress.accumulated_text
      : output.text;
  const piece = textNow.trim();
  if (!piece) return [...segments];
  const id = output.segment_id !== undefined ? `s${output.segment_id}` : `t${segments.length}:${piece}`;
  const last = segments.at(-1);
  const next =
    last && last.id === id ? [...segments.slice(0, -1), { id, text: piece }] : [...segments, { id, text: piece }];
  return next.slice(-CAPTION_SEGMENTS);
}

export function captionText(segments: readonly CaptionSegment[]): string {
  return segments.map((s) => s.text).join(" ");
}

// --- what went wrong ----------------------------------------------------------------------------------------------

function statusOf(error: unknown): number | null {
  if (typeof error !== "object" || error === null || !("status" in error)) return null;
  const status = (error as { status: unknown }).status;
  return typeof status === "number" ? status : null;
}

function nameOf(error: unknown): string {
  if (typeof error !== "object" || error === null || !("name" in error)) return "";
  const name = (error as { name: unknown }).name;
  return typeof name === "string" ? name : "";
}

/** Why the microphone could not be opened, in the candidate's terms. */
export function micErrorMessage(error: unknown): string {
  switch (nameOf(error)) {
    case "NotAllowedError":
    case "SecurityError":
      return "Microphone access is blocked. Allow it in your browser's site settings, or continue in text.";
    case "NotFoundError":
    case "OverconstrainedError":
      return "No microphone was found. Connect one and try again, or continue in text.";
    case "NotReadableError":
      return "The microphone is in use by another application. Close it and try again, or continue in text.";
    default:
      return "The microphone could not be opened. You can try again or continue in text.";
  }
}

/** Why the voice connection failed; text mode is always the fallback. */
export function voiceErrorMessage(error: unknown): string {
  const status = statusOf(error);
  if (status === 401 || status === 403) return "Voice could not verify this interview link. Please continue in text.";
  if (status === 409) return "Voice is not available for this interview right now. Please continue in text.";
  if (nameOf(error) === "VoiceTimeoutError") {
    return "Voice took too long to connect (a network or firewall may block it). Please continue in text.";
  }
  return "Voice could not connect. Please continue in text; your progress is kept.";
}

export class VoiceTimeoutError extends Error {
  constructor() {
    super("The voice connection timed out.");
    this.name = "VoiceTimeoutError";
  }
}
