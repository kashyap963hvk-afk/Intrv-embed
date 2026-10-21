"use client";

import type { FaceLandmarker, FaceLandmarkerResult } from "@mediapipe/tasks-vision";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  cameraClose,
  cameraFlush,
  cameraStep,
  DETECT_INTERVAL_MS,
  FACE_MODEL_PATH,
  faceLandmarkerOptions,
  INITIAL_CAMERA,
  isAcceptableSnapshot,
  isMediapipeTelemetry,
  MEDIAPIPE_WASM_PATH,
  observe,
  SNAPSHOT_INTERVAL_MS,
  SNAPSHOT_QUALITY,
  SNAPSHOT_SIZE,
  virtualCameraEvent,
} from "@/lib/camera-integrity";
import type { BrowserEvent } from "@/lib/interviews";

export type CameraStatus = "off" | "starting" | "on" | "error";

export interface CameraMonitor {
  status: CameraStatus;
  error: string | null;
  /** The camera stream, for the candidate's self-view. */
  stream: MediaStream | null;
  /** Faces in view at the last check (null before the first one). */
  faces: number | null;
  /** Report episodes in progress now (before the candidate ends the interview, while it is still live). */
  flush: () => void;
}

export interface CameraMonitorOptions {
  enabled: boolean;
  /** Queue a derived integrity event (the room's integrity batching). */
  record: (event: BrowserEvent) => void;
  /** STRICT mode: send a low-resolution JPEG to the reviewer; null when snapshots are off. */
  snapshot: ((jpeg: Blob) => Promise<boolean>) | null;
}

let telemetryBlocked = false;

/**
 * MediaPipe Tasks posts usage statistics to Google with the global fetch, and has no option to turn that off.
 * Answer those requests locally with 403, which also makes its logger stop for good.
 */
function blockMediapipeTelemetry(): void {
  if (telemetryBlocked) return;
  telemetryBlocked = true;
  const original = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) =>
    isMediapipeTelemetry(input) ? Promise.resolve(new Response(null, { status: 403 })) : original(input, init);
}

/**
 * The MediaPipe WebAssembly module writes informational lines ("INFO: Created TensorFlow Lite XNNPACK delegate")
 * to stderr, which Emscripten prints with console.error. Its loader merges a pre-set global `Module`, so stderr is
 * routed here: informational lines are dropped and anything else stays visible as a warning.
 */
function quietMediapipeStderr(): void {
  Object.assign(self, {
    Module: {
      printErr: (line: string) => {
        if (!line.startsWith("INFO:")) console.warn(line);
      },
    },
  });
}

/** Event-triggered snapshots are spaced at least this far apart. */
const MIN_EVENT_SNAPSHOT_GAP_MS = 10_000;

function cameraErrorMessage(error: unknown): string {
  const name = error instanceof DOMException ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "Camera access is blocked. Allow it in your browser's site settings and reload the page.";
  }
  if (name === "NotFoundError" || name === "OverconstrainedError") return "No camera was found.";
  if (name === "NotReadableError") return "The camera is in use by another application.";
  return "The camera check could not start.";
}

function toJpeg(canvas: HTMLCanvasElement, quality: number): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
}

/** A low-resolution JPEG of the current frame that the API accepts, or null. */
async function snapshotOf(video: HTMLVideoElement): Promise<Blob | null> {
  const canvas = document.createElement("canvas");
  canvas.width = SNAPSHOT_SIZE.width;
  canvas.height = SNAPSHOT_SIZE.height;
  const context = canvas.getContext("2d");
  if (!context) return null;
  context.drawImage(video, 0, 0, canvas.width, canvas.height);
  for (const quality of [SNAPSHOT_QUALITY, 0.4]) {
    const blob = await toJpeg(canvas, quality);
    if (blob && isAcceptableSnapshot(new Uint8Array(await blob.arrayBuffer()))) return blob;
  }
  return null;
}

/**
 * The in-browser camera check (ADR 0005): face count and head direction from a self-hosted MediaPipe face
 * landmarker, about three times a second on a timer. Only derived events (and STRICT snapshots) leave the device.
 */
export function useCameraMonitor({ enabled, record, snapshot }: CameraMonitorOptions): CameraMonitor {
  const [status, setStatus] = useState<CameraStatus>("off");
  const [error, setError] = useState<string | null>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [faces, setFaces] = useState<number | null>(null);
  const recordRef = useRef(record);
  const snapshotRef = useRef(snapshot);
  const flushRef = useRef<() => void>(() => undefined);

  useEffect(() => {
    recordRef.current = record;
    snapshotRef.current = snapshot;
  });

  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    let opened: MediaStream | null = null;
    let detectTimer: ReturnType<typeof setInterval> | null = null;
    let snapshotTimer: ReturnType<typeof setInterval> | null = null;
    let landmarker: FaceLandmarker | null = null;
    let state = INITIAL_CAMERA;
    let lastFaces: number | null = null;
    let lastEventSnapshot = 0;
    flushRef.current = () => {
      const closed = cameraClose(state);
      state = closed.state;
      for (const event of closed.events) recordRef.current(event);
    };
    const video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;

    const sendSnapshot = async (fromEvent: boolean) => {
      const send = snapshotRef.current;
      if (!send || stopped || video.readyState < 2) return;
      const now = Date.now();
      if (fromEvent && now - lastEventSnapshot < MIN_EVENT_SNAPSHOT_GAP_MS) return;
      if (fromEvent) lastEventSnapshot = now;
      const jpeg = await snapshotOf(video);
      if (jpeg && !stopped) await send(jpeg);
    };

    const detect = () => {
      if (!landmarker || stopped || video.readyState < 2) return;
      let result: FaceLandmarkerResult;
      try {
        result = landmarker.detectForVideo(video, performance.now());
      } catch {
        return;
      }
      const observation = observe(result.faceLandmarks);
      const step = cameraStep(state, observation, Date.now());
      state = step.state;
      for (const event of step.events) recordRef.current(event);
      if (step.snapshot) void sendSnapshot(true);
      if (observation.faces !== lastFaces) {
        lastFaces = observation.faces;
        setFaces(observation.faces);
      }
    };

    const start = async () => {
      setStatus("starting");
      setError(null);
      try {
        opened = await navigator.mediaDevices.getUserMedia({
          video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: "user" },
          audio: false,
        });
      } catch (caught) {
        if (!stopped) {
          setStatus("error");
          setError(cameraErrorMessage(caught));
        }
        return;
      }
      if (stopped) {
        for (const track of opened.getTracks()) track.stop();
        return;
      }
      const track = opened.getVideoTracks()[0];
      const virtual = track ? virtualCameraEvent(track.label) : null;
      if (virtual) recordRef.current(virtual);
      track?.addEventListener("ended", () => {
        if (stopped) return;
        setStatus("error");
        setError("The camera stopped. Reload the page to turn it back on.");
      });
      setStream(opened);
      video.srcObject = opened;
      await video.play().catch(() => undefined);

      try {
        blockMediapipeTelemetry();
        const vision = await import("@mediapipe/tasks-vision");
        const fileset = await vision.FilesetResolver.forVisionTasks(MEDIAPIPE_WASM_PATH);
        quietMediapipeStderr();
        const created = await vision.FaceLandmarker.createFromOptions(fileset, faceLandmarkerOptions(FACE_MODEL_PATH));
        if (stopped) {
          created.close();
          return;
        }
        landmarker = created;
      } catch {
        if (!stopped) {
          setStatus("error");
          setError("The camera check could not load. The interview continues without it.");
        }
        return;
      }
      setStatus("on");
      detectTimer = setInterval(detect, DETECT_INTERVAL_MS);
      if (snapshotRef.current) {
        void sendSnapshot(false);
        snapshotTimer = setInterval(() => void sendSnapshot(false), SNAPSHOT_INTERVAL_MS);
      }
    };

    void start();
    return () => {
      stopped = true;
      flushRef.current = () => undefined;
      if (detectTimer) clearInterval(detectTimer);
      if (snapshotTimer) clearInterval(snapshotTimer);
      for (const event of cameraFlush(state)) recordRef.current(event);
      landmarker?.close();
      landmarker = null;
      video.srcObject = null;
      if (opened) for (const track of opened.getTracks()) track.stop();
      setStream(null);
      setFaces(null);
      setStatus("off");
    };
  }, [enabled]);

  const flush = useCallback(() => flushRef.current(), []);
  return { status, error, stream, faces, flush };
}
