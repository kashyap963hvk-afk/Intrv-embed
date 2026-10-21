import { describe, expect, it } from "vitest";

import {
  ABSENT_AFTER_MS,
  AWAY_AFTER_MS,
  awayDirection,
  CALIBRATION_FRAMES,
  cameraClose,
  cameraFlush,
  cameraMonitoringOn,
  cameraStep,
  DETECT_INTERVAL_MS,
  END_GRACE_MS,
  episodeStep,
  FACE_MODEL_PATH,
  faceLandmarkerOptions,
  headPose,
  INITIAL_CAMERA,
  isAcceptableSnapshot,
  isMediapipeTelemetry,
  LANDMARK,
  MULTIPLE_AFTER_MS,
  NO_EPISODE,
  observe,
  SNAPSHOT_MAX_BYTES,
  virtualCameraEvent,
  virtualCameraMarker,
  type CameraState,
  type HeadPose,
  type Observation,
  type Point,
} from "./camera-integrity";
import type { BrowserEvent } from "./interviews";

/** A synthetic 478-point face mesh: a frontal face, with the nose moved by (dx, dy) to turn the head. */
function face(dx = 0, dy = 0): Point[] {
  const points: Point[] = Array.from({ length: 478 }, () => ({ x: 0.5, y: 0.5 }));
  points[LANDMARK.edgeA] = { x: 0.4, y: 0.5 };
  points[LANDMARK.edgeB] = { x: 0.6, y: 0.5 };
  points[LANDMARK.forehead] = { x: 0.5, y: 0.35 };
  points[LANDMARK.chin] = { x: 0.5, y: 0.7 };
  points[LANDMARK.eyeA] = { x: 0.45, y: 0.45 };
  points[LANDMARK.eyeB] = { x: 0.55, y: 0.45 };
  points[LANDMARK.noseTip] = { x: 0.5 + dx, y: 0.52 + dy };
  return points;
}

/** Feed observations every DETECT_INTERVAL_MS from `start` for `ms` milliseconds; collect events and snapshots. */
function feed(
  state: CameraState,
  observation: Observation,
  start: number,
  ms: number,
): { state: CameraState; events: BrowserEvent[]; snapshots: number; end: number } {
  const events: BrowserEvent[] = [];
  let snapshots = 0;
  let now = start;
  let current = state;
  for (; now < start + ms; now += DETECT_INTERVAL_MS) {
    const step = cameraStep(current, observation, now);
    current = step.state;
    events.push(...step.events);
    if (step.snapshot) snapshots += 1;
  }
  return { state: current, events, snapshots, end: now };
}

const FRONTAL: Observation = { faces: 1, pose: headPose(face()) };

/** A monitor that has learnt the candidate's straight-ahead pose. */
function calibrated(): { state: CameraState; end: number } {
  const run = feed(INITIAL_CAMERA, FRONTAL, 0, CALIBRATION_FRAMES * DETECT_INTERVAL_MS);
  expect(run.state.baseline).not.toBeNull();
  return { state: run.state, end: run.end };
}

describe("the face landmarker options", () => {
  it("never requests blendshapes (no expression analysis) or transformation matrices", () => {
    const options = faceLandmarkerOptions(FACE_MODEL_PATH);
    expect(options.outputFaceBlendshapes).toBe(false);
    expect(options.outputFacialTransformationMatrixes).toBe(false);
    // Nothing else in the options may turn blendshapes on.
    const truthyBlendshapeKeys = Object.entries(options).filter(([key, value]) => /blendshape/i.test(key) && value);
    expect(truthyBlendshapeKeys).toEqual([]);
  });

  it("loads the self-hosted model in video mode and looks for more than one face", () => {
    const options = faceLandmarkerOptions("/models/face_landmarker.task");
    expect(options.baseOptions?.modelAssetPath).toBe("/models/face_landmarker.task");
    expect(options.runningMode).toBe("VIDEO");
    expect(options.numFaces).toBeGreaterThanOrEqual(2);
  });
});

describe("head direction", () => {
  it("reads a frontal face as centred and a turned or lowered head as offsets", () => {
    const frontal = headPose(face());
    expect(frontal?.yaw).toBeCloseTo(0, 5);
    const turned = headPose(face(0.05));
    expect(turned?.yaw).toBeCloseTo(0.25, 5);
    const lowered = headPose(face(0, 0.05));
    expect((lowered?.pitch ?? 0) - (frontal?.pitch ?? 0)).toBeCloseTo(0.05 / 0.35, 5);
  });

  it("gives no pose for missing landmarks or a tiny face", () => {
    expect(headPose([])).toBeNull();
    const tiny = face().map((p) => ({ x: 0.5 + (p.x - 0.5) / 100, y: 0.5 + (p.y - 0.5) / 100 }));
    expect(headPose(tiny)).toBeNull();
  });

  it("names the direction from the candidate's side (the camera sees them mirrored)", () => {
    const base: HeadPose = { yaw: 0, pitch: 0.2 };
    expect(awayDirection({ yaw: 0.05, pitch: 0.21 }, base)).toBeNull();
    expect(awayDirection({ yaw: 0.3, pitch: 0.2 }, base)).toBe("left");
    expect(awayDirection({ yaw: -0.3, pitch: 0.2 }, base)).toBe("right");
    expect(awayDirection({ yaw: 0, pitch: 0.4 }, base)).toBe("down");
    expect(awayDirection({ yaw: 0, pitch: 0.05 }, base)).toBe("up");
  });

  it("observes head pose only when exactly one face is in view", () => {
    expect(observe([])).toEqual({ faces: 0, pose: null });
    expect(observe([face()]).pose).not.toBeNull();
    expect(observe([face(), face()])).toEqual({ faces: 2, pose: null });
  });
});

describe("episodes", () => {
  it("counts a condition after its threshold and reports its duration once it is gone", () => {
    let step = episodeStep(NO_EPISODE, true, 0, 5_000);
    expect(step.started).toBe(false);
    step = episodeStep(step.episode, true, 4_999, 5_000);
    expect(step.started).toBe(false);
    step = episodeStep(step.episode, true, 5_000, 5_000);
    expect(step.started).toBe(true);
    step = episodeStep(step.episode, true, 6_000, 5_000);
    expect(step.started).toBe(false); // started only once
    step = episodeStep(step.episode, false, 6_500, 5_000);
    expect(step.endedMs).toBeNull(); // still within the grace period
    step = episodeStep(step.episode, false, 6_000 + END_GRACE_MS, 5_000);
    expect(step.endedMs).toBe(6_000);
    expect(step.episode).toEqual(NO_EPISODE);
  });

  it("ignores a short condition and is not split by one missed frame", () => {
    let step = episodeStep(NO_EPISODE, true, 0, 5_000);
    step = episodeStep(step.episode, false, 2_000, 5_000);
    expect(step).toMatchObject({ endedMs: null, episode: NO_EPISODE });

    step = episodeStep(NO_EPISODE, true, 0, 5_000);
    step = episodeStep(step.episode, true, 3_000, 5_000);
    step = episodeStep(step.episode, false, 3_300, 5_000); // one frame without the condition
    step = episodeStep(step.episode, true, 3_600, 5_000);
    step = episodeStep(step.episode, true, 6_000, 5_000);
    expect(step.started).toBe(true);
    expect(step.episode.since).toBe(0);
  });
});

describe("the camera monitor", () => {
  it("reports nothing while one face looks at the screen", () => {
    const { state, end } = calibrated();
    const run = feed(state, FRONTAL, end, 60_000);
    expect(run.events).toEqual([]);
    expect(run.snapshots).toBe(0);
  });

  it("reports an empty seat after about five seconds, with its duration", () => {
    const { state, end } = calibrated();
    const short = feed(state, { faces: 0, pose: null }, end, ABSENT_AFTER_MS - 1_000);
    const back = feed(short.state, FRONTAL, short.end, 3_000);
    expect([...short.events, ...back.events]).toEqual([]); // under five seconds: nothing

    const away = feed(back.state, { faces: 0, pose: null }, back.end, 8_000);
    expect(away.snapshots).toBe(1); // a STRICT snapshot when the episode starts
    const returned = feed(away.state, FRONTAL, away.end, 3_000);
    expect(returned.events).toHaveLength(1);
    const [event] = returned.events;
    expect(event.kind).toBe("camera_face_absent");
    expect(event.duration_ms).toBeGreaterThanOrEqual(7_600);
    expect(event.duration_ms).toBeLessThanOrEqual(8_000);
  });

  it("reports a second person in view with the most faces seen", () => {
    const { state, end } = calibrated();
    const two = feed(state, { faces: 2, pose: null }, end, MULTIPLE_AFTER_MS + 1_000);
    const three = feed(two.state, { faces: 3, pose: null }, two.end, 1_000);
    const alone = feed(three.state, FRONTAL, three.end, 2_000);
    expect(alone.events).toEqual([
      { kind: "camera_multiple_faces", duration_ms: expect.any(Number), detail: { faces: 3 } },
    ]);
    expect(two.snapshots + three.snapshots).toBe(1);
  });

  it("reports looking away for more than five seconds with its direction", () => {
    const { state, end } = calibrated();
    const glance = feed(state, { faces: 1, pose: headPose(face(0.06)) }, end, 2_000);
    const front = feed(glance.state, FRONTAL, glance.end, 2_000);
    expect([...glance.events, ...front.events]).toEqual([]); // a glance is not a signal

    const turned = feed(front.state, { faces: 1, pose: headPose(face(0.06)) }, front.end, AWAY_AFTER_MS + 2_000);
    const back = feed(turned.state, FRONTAL, turned.end, 2_000);
    expect(back.events).toHaveLength(1);
    expect(back.events[0]).toMatchObject({ kind: "camera_gaze_away", detail: { direction: "left" } });
    expect(back.events[0].duration_ms).toBeGreaterThanOrEqual(AWAY_AFTER_MS);
  });

  it("judges direction against the candidate's own straight-ahead pose", () => {
    // A camera mounted to the side: the candidate's normal pose is slightly turned.
    const offset: Observation = { faces: 1, pose: headPose(face(0.02)) };
    const learnt = feed(INITIAL_CAMERA, offset, 0, CALIBRATION_FRAMES * DETECT_INTERVAL_MS);
    expect(learnt.state.baseline?.yaw).toBeCloseTo(0.1, 5);
    const steady = feed(learnt.state, offset, learnt.end, 20_000);
    expect(steady.events).toEqual([]);
  });

  it("does not judge direction before calibration, nor learn from a turned head", () => {
    const turned: Observation = { faces: 1, pose: headPose(face(0.06)) };
    const run = feed(INITIAL_CAMERA, turned, 0, 20_000);
    expect(run.state.baseline).toBeNull();
    expect(run.events).toEqual([]);
  });

  it("reports episodes still running when the camera stops", () => {
    const { state, end } = calibrated();
    const gone = feed(state, { faces: 0, pose: null }, end, 7_000);
    expect(cameraFlush(gone.state)).toEqual([{ kind: "camera_face_absent", duration_ms: expect.any(Number) }]);
    expect(cameraFlush(INITIAL_CAMERA)).toEqual([]);
  });

  it("reports episodes in progress before the candidate ends, once, and keeps the learnt pose", () => {
    const { state, end } = calibrated();
    const gone = feed(state, { faces: 0, pose: null }, end, 7_000);
    const closed = cameraClose(gone.state);
    expect(closed.events.map((e) => e.kind)).toEqual(["camera_face_absent"]);
    expect(closed.state.baseline).toEqual(state.baseline);
    expect(cameraFlush(closed.state)).toEqual([]); // not reported twice when the camera then stops
  });

  it("only ever emits camera event kinds with counts, directions and durations", () => {
    const { state, end } = calibrated();
    const runs = [
      feed(state, { faces: 0, pose: null }, end, 7_000),
      feed(state, { faces: 2, pose: null }, end, 7_000),
      feed(state, { faces: 1, pose: headPose(face(0, 0.06)) }, end, 7_000),
    ];
    const events = runs.flatMap((run) => [...run.events, ...cameraFlush(run.state)]);
    expect(events.map((e) => e.kind).sort()).toEqual([
      "camera_face_absent",
      "camera_gaze_away",
      "camera_multiple_faces",
    ]);
    for (const event of events) {
      expect(Object.keys(event.detail ?? {}).every((key) => key === "faces" || key === "direction")).toBe(true);
    }
  });
});

describe("devices and snapshots", () => {
  it("recognises virtual cameras by their label and sends only the marker", () => {
    expect(virtualCameraMarker("OBS Virtual Camera")).toBe("obs");
    expect(virtualCameraMarker("ManyCam Virtual Webcam")).toBe("manycam");
    expect(virtualCameraMarker("Integrated Webcam (0bda:58f4)")).toBeNull();
    expect(virtualCameraMarker("Logitech BRIO")).toBeNull();
    expect(virtualCameraMarker("Jobs camera")).toBeNull(); // "obs" inside a word is not OBS
    expect(virtualCameraEvent("OBS Virtual Camera")).toEqual({ kind: "virtual_camera", detail: { marker: "obs" } });
    expect(virtualCameraEvent("FaceTime HD Camera")).toBeNull();
  });

  it("accepts only small JPEGs as snapshots", () => {
    expect(isAcceptableSnapshot(new Uint8Array([0xff, 0xd8, 0xff, 0xe0]))).toBe(true);
    expect(isAcceptableSnapshot(new Uint8Array([0x89, 0x50, 0x4e, 0x47]))).toBe(false);
    expect(isAcceptableSnapshot(new Uint8Array(SNAPSHOT_MAX_BYTES + 1).fill(0xff))).toBe(false);
    expect(isAcceptableSnapshot(new Uint8Array([0xff]))).toBe(false);
  });

  it("recognises MediaPipe's usage telemetry so it can be kept on the device", () => {
    expect(isMediapipeTelemetry("https://odml.pa.googleapis.com/v1/log")).toBe(true);
    expect(isMediapipeTelemetry(new URL("https://odml.pa.googleapis.com/v1/log"))).toBe(true);
    expect(isMediapipeTelemetry(new Request("https://odml.pa.googleapis.com/v1/log"))).toBe(true);
    expect(isMediapipeTelemetry("http://localhost:8000/api/join/t/integrity")).toBe(false);
    expect(isMediapipeTelemetry("/models/face_landmarker.task")).toBe(false);
  });

  it("runs only with the setting on, integrity monitoring on and the candidate's consent", () => {
    expect(cameraMonitoringOn("optional", "STRICT", true)).toBe(true);
    expect(cameraMonitoringOn("required", "STANDARD", true)).toBe(true);
    expect(cameraMonitoringOn("optional", "STRICT", false)).toBe(false);
    expect(cameraMonitoringOn("off", "STRICT", true)).toBe(false);
    expect(cameraMonitoringOn("required", "OFF", true)).toBe(false);
  });
});
