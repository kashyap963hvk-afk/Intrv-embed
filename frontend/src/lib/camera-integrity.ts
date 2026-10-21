/**
 * Camera integrity signals, computed in the browser only (ADR 0005, ARCHITECTURE §15.1).
 *
 * Only these are derived from the camera: how many faces are in view, and whether the head is turned away from the
 * screen (direction and duration). There is no emotion, expression, identity, age, gender or "confidence" analysis:
 * the face landmarker never outputs blendshapes, and nothing but the derived events below (and, in STRICT mode,
 * low-resolution snapshots for the human reviewer) leaves the device. The signals go to a human reviewer and never
 * into a score.
 */

import type { FaceLandmarkerOptions } from "@mediapipe/tasks-vision";

import type { BrowserEvent } from "@/lib/interviews";

/** Self-hosted MediaPipe files (copied from node_modules by scripts/copy-vendor-assets.mjs). */
export const MEDIAPIPE_WASM_PATH = "/vendor/mediapipe/wasm";
/** The face landmarker model, self-hosted so offline and on-prem deployments work. */
export const FACE_MODEL_PATH = "/models/face_landmarker.task";

/** Detection runs about three times a second, on a timer outside React rendering. */
export const DETECT_INTERVAL_MS = 333;
export const ABSENT_AFTER_MS = 5_000;
export const MULTIPLE_AFTER_MS = 2_000;
export const AWAY_AFTER_MS = 5_000;
/** A condition must be gone this long before its episode ends (one missed frame does not split it). */
export const END_GRACE_MS = 1_000;
const MAX_DURATION_MS = 3_600_000;

/** STRICT mode: a snapshot every minute and when a camera episode starts. */
export const SNAPSHOT_INTERVAL_MS = 60_000;
export const SNAPSHOT_SIZE = { width: 320, height: 240 } as const;
export const SNAPSHOT_QUALITY = 0.6;
export const SNAPSHOT_MAX_BYTES = 300 * 1024;

/**
 * The face landmarker's options: landmarks only. Blendshapes (expression coefficients) and transformation matrices
 * are never requested; head direction comes from a few landmark positions.
 */
export function faceLandmarkerOptions(modelAssetPath: string): FaceLandmarkerOptions {
  return {
    baseOptions: { modelAssetPath, delegate: "CPU" },
    runningMode: "VIDEO",
    numFaces: 3,
    minFaceDetectionConfidence: 0.5,
    minFacePresenceConfidence: 0.5,
    minTrackingConfidence: 0.5,
    outputFaceBlendshapes: false,
    outputFacialTransformationMatrixes: false,
  };
}

// --- head direction -------------------------------------------------------------------------------------------

export interface Point {
  x: number;
  y: number;
}

/** Face-mesh landmark indices: nose tip, the face's outer edges at cheek level, forehead, chin and eye corners. */
export const LANDMARK = {
  noseTip: 1,
  edgeA: 234,
  edgeB: 454,
  forehead: 10,
  chin: 152,
  eyeA: 33,
  eyeB: 263,
} as const;

export interface HeadPose {
  /** Nose offset from the face's centre line, in face widths (0 facing the camera; positive: turned to image +x). */
  yaw: number;
  /** Nose position below the eye line, in face heights (grows when looking down). */
  pitch: number;
}

/** Head direction from landmark positions, or null when the face is too small or the landmarks are missing. */
export function headPose(landmarks: readonly Point[]): HeadPose | null {
  const pick = (index: number) => landmarks[index];
  const nose = pick(LANDMARK.noseTip);
  const a = pick(LANDMARK.edgeA);
  const b = pick(LANDMARK.edgeB);
  const top = pick(LANDMARK.forehead);
  const chin = pick(LANDMARK.chin);
  const eyeA = pick(LANDMARK.eyeA);
  const eyeB = pick(LANDMARK.eyeB);
  if (!nose || !a || !b || !top || !chin || !eyeA || !eyeB) return null;
  const width = Math.abs(b.x - a.x);
  const height = Math.abs(chin.y - top.y);
  if (width < 0.02 || height < 0.02) return null;
  const centre = (a.x + b.x) / 2;
  const eyeLine = (eyeA.y + eyeB.y) / 2;
  return { yaw: (nose.x - centre) / width, pitch: (nose.y - eyeLine) / height };
}

export type Direction = "left" | "right" | "up" | "down";

/** How far from the candidate's own straight-ahead pose counts as looking away. */
export const AWAY_LIMITS = { yaw: 0.18, pitch: 0.1 } as const;

/** Frames used to learn the candidate's straight-ahead pose before head direction is judged. */
export const CALIBRATION_FRAMES = 9;
/** A calibration that is itself turned this far is not trusted (the candidate was not facing the screen). */
const CALIBRATION_MAX_YAW = 0.15;

/**
 * Direction away from the screen, or null when facing it. Directions are the candidate's own: the camera sees them
 * mirrored, so a nose moving to image +x means they turned to their left.
 */
export function awayDirection(pose: HeadPose, baseline: HeadPose): Direction | null {
  const dYaw = pose.yaw - baseline.yaw;
  const dPitch = pose.pitch - baseline.pitch;
  const yawOut = Math.abs(dYaw) / AWAY_LIMITS.yaw;
  const pitchOut = Math.abs(dPitch) / AWAY_LIMITS.pitch;
  if (yawOut <= 1 && pitchOut <= 1) return null;
  if (yawOut >= pitchOut) return dYaw > 0 ? "left" : "right";
  return dPitch > 0 ? "down" : "up";
}

function median(values: readonly number[]): number {
  const sorted = [...values].sort((x, y) => x - y);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

// --- episodes -------------------------------------------------------------------------------------------------

/** One condition over time (no face, several faces, looking away): when it began and when it was last seen. */
export interface Episode {
  since: number | null;
  lastSeen: number | null;
  /** The episode lasted long enough to count (it is reported when it ends). */
  counted: boolean;
}

export const NO_EPISODE: Episode = { since: null, lastSeen: null, counted: false };

export interface EpisodeStep {
  episode: Episode;
  /** The episode just reached its threshold (the moment for a STRICT snapshot). */
  started: boolean;
  /** A counted episode ended: how long it lasted. */
  endedMs: number | null;
}

/**
 * Advance one condition. It counts once it has held for `after` ms, and ends (reporting its duration) once it has
 * been gone for END_GRACE_MS; shorter flickers are ignored.
 */
export function episodeStep(episode: Episode, active: boolean, now: number, after: number): EpisodeStep {
  if (active) {
    const since = episode.since ?? now;
    const reached = !episode.counted && now - since >= after;
    return { episode: { since, lastSeen: now, counted: episode.counted || reached }, started: reached, endedMs: null };
  }
  if (episode.since === null || episode.lastSeen === null) return { episode, started: false, endedMs: null };
  if (now - episode.lastSeen < END_GRACE_MS) return { episode, started: false, endedMs: null };
  const endedMs = episode.counted ? Math.min(MAX_DURATION_MS, Math.round(episode.lastSeen - episode.since)) : null;
  return { episode: NO_EPISODE, started: false, endedMs };
}

/** Close an episode now (the camera stops): a counted one is reported up to the last time it was seen. */
function episodeClose(episode: Episode): number | null {
  if (!episode.counted || episode.since === null || episode.lastSeen === null) return null;
  return Math.min(MAX_DURATION_MS, Math.round(episode.lastSeen - episode.since));
}

// --- the monitor ----------------------------------------------------------------------------------------------

export interface CameraState {
  absent: Episode;
  multiple: Episode;
  away: Episode;
  /** Most faces seen during the current multiple-faces episode. */
  maxFaces: number;
  /** Direction when the current looking-away episode began. */
  direction: Direction | null;
  calibration: HeadPose[];
  baseline: HeadPose | null;
}

export const INITIAL_CAMERA: CameraState = {
  absent: NO_EPISODE,
  multiple: NO_EPISODE,
  away: NO_EPISODE,
  maxFaces: 0,
  direction: null,
  calibration: [],
  baseline: null,
};

/** What one frame showed: the number of faces and, for a single face, its head pose. */
export interface Observation {
  faces: number;
  pose: HeadPose | null;
}

export interface CameraStep {
  state: CameraState;
  events: BrowserEvent[];
  /** An episode just started: STRICT mode takes a snapshot for the reviewer. */
  snapshot: boolean;
}

function calibrate(state: CameraState, pose: HeadPose | null): Pick<CameraState, "calibration" | "baseline"> {
  if (state.baseline || !pose) return { calibration: state.calibration, baseline: state.baseline };
  const calibration = [...state.calibration, pose];
  if (calibration.length < CALIBRATION_FRAMES) return { calibration, baseline: null };
  const baseline = { yaw: median(calibration.map((p) => p.yaw)), pitch: median(calibration.map((p) => p.pitch)) };
  // Started out turned away: try again with the next frames.
  if (Math.abs(baseline.yaw) > CALIBRATION_MAX_YAW) return { calibration: [], baseline: null };
  return { calibration: [], baseline };
}

/** Fold one frame's observation into the monitor: derived events only (counts, direction, durations). */
export function cameraStep(state: CameraState, observation: Observation, now: number): CameraStep {
  const events: BrowserEvent[] = [];
  const faces = Math.max(0, Math.floor(observation.faces));
  const single = faces === 1 ? observation.pose : null;
  const { calibration, baseline } = calibrate(state, single);
  const direction = single && baseline ? awayDirection(single, baseline) : null;

  const absent = episodeStep(state.absent, faces === 0, now, ABSENT_AFTER_MS);
  const multiple = episodeStep(state.multiple, faces >= 2, now, MULTIPLE_AFTER_MS);
  const away = episodeStep(state.away, direction !== null, now, AWAY_AFTER_MS);

  if (absent.endedMs !== null) events.push({ kind: "camera_face_absent", duration_ms: absent.endedMs });
  if (multiple.endedMs !== null) {
    events.push({
      kind: "camera_multiple_faces",
      duration_ms: multiple.endedMs,
      detail: { faces: Math.max(2, state.maxFaces) },
    });
  }
  if (away.endedMs !== null) {
    events.push({
      kind: "camera_gaze_away",
      duration_ms: away.endedMs,
      detail: { direction: state.direction ?? "away" },
    });
  }

  const next: CameraState = {
    absent: absent.episode,
    multiple: multiple.episode,
    away: away.episode,
    maxFaces: multiple.episode.since === null ? 0 : Math.max(state.maxFaces, faces),
    direction: away.episode.since === null ? null : (state.direction ?? direction),
    calibration,
    baseline,
  };
  return { state: next, events, snapshot: absent.started || multiple.started || away.started };
}

/** The camera stops (the interview ended or the page closes): report episodes still in progress. */
export function cameraFlush(state: CameraState): BrowserEvent[] {
  const events: BrowserEvent[] = [];
  const absent = episodeClose(state.absent);
  const multiple = episodeClose(state.multiple);
  const away = episodeClose(state.away);
  if (absent !== null) events.push({ kind: "camera_face_absent", duration_ms: absent });
  if (multiple !== null) {
    events.push({
      kind: "camera_multiple_faces",
      duration_ms: multiple,
      detail: { faces: Math.max(2, state.maxFaces) },
    });
  }
  if (away !== null) {
    events.push({ kind: "camera_gaze_away", duration_ms: away, detail: { direction: state.direction ?? "away" } });
  }
  return events;
}

/** Report episodes still in progress and start fresh (the candidate ends the interview; the baseline stays). */
export function cameraClose(state: CameraState): { state: CameraState; events: BrowserEvent[] } {
  return {
    state: { ...state, absent: NO_EPISODE, multiple: NO_EPISODE, away: NO_EPISODE, maxFaces: 0, direction: null },
    events: cameraFlush(state),
  };
}

/** The observation for one landmarker result (face count; head pose only for a single face). */
export function observe(faceLandmarks: readonly (readonly Point[])[]): Observation {
  const faces = faceLandmarks.length;
  return { faces, pose: faces === 1 ? headPose(faceLandmarks[0]) : null };
}

// --- devices and snapshots ------------------------------------------------------------------------------------

const VIRTUAL_CAMERA_MARKERS = [
  "obs",
  "manycam",
  "virtual",
  "snap camera",
  "xsplit",
  "camtwist",
  "splitcam",
  "droidcam",
  "epoccam",
  "mmhmm",
  "e2esoft",
  "youcam",
] as const;

/** The marker a camera's label matches when it looks like a virtual camera (software feeding the video), or null. */
export function virtualCameraMarker(label: string): string | null {
  const lower = label.toLowerCase();
  for (const marker of VIRTUAL_CAMERA_MARKERS) {
    const pattern = marker === "obs" ? /\bobs\b/ : null;
    if (pattern ? pattern.test(lower) : lower.includes(marker)) return marker;
  }
  return null;
}

/** The integrity event for a virtual camera (only the matched marker leaves the device, never the full label). */
export function virtualCameraEvent(label: string): BrowserEvent | null {
  const marker = virtualCameraMarker(label);
  return marker ? { kind: "virtual_camera", detail: { marker } } : null;
}

/** A snapshot the API accepts: a JPEG (starts with FF D8) of at most 300 KB. */
export function isAcceptableSnapshot(bytes: Uint8Array): boolean {
  return bytes.length >= 2 && bytes.length <= SNAPSHOT_MAX_BYTES && bytes[0] === 0xff && bytes[1] === 0xd8;
}

/** Hosts MediaPipe's built-in usage logger posts to; blocked so nothing about the camera check leaves the device. */
const TELEMETRY_HOSTS = new Set(["odml.pa.googleapis.com"]);

/** Whether a fetch is MediaPipe's usage telemetry (never allowed to leave the browser). */
export function isMediapipeTelemetry(input: RequestInfo | URL): boolean {
  const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  try {
    return TELEMETRY_HOSTS.has(new URL(raw).hostname);
  } catch {
    return false;
  }
}

/** The camera check runs when the interview's camera setting is on, integrity is not OFF and the candidate agreed. */
export function cameraMonitoringOn(setting: string, integrityMode: string, consented: boolean): boolean {
  return setting !== "off" && integrityMode !== "OFF" && consented;
}
