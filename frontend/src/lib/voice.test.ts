import { describe, expect, it } from "vitest";

import {
  CAPTION_SEGMENTS,
  captionStep,
  captionText,
  deviceLabel,
  micConstraints,
  micErrorMessage,
  micLevel,
  parseVoiceInfo,
  relayedFrame,
  voiceErrorMessage,
  VoiceTimeoutError,
  type BotOutput,
  type CaptionSegment,
} from "./voice";

describe("voice connection details", () => {
  it("reads the offer URL and ICE servers", () => {
    const info = parseVoiceInfo({
      available: true,
      offer_url: "https://voice.example.com/voice/s1/offer",
      ice_servers: [
        { urls: ["stun:stun.example.com:3478"] },
        { urls: ["turn:turn.example.com:3478"], username: "1700000000:s1", credential: "c2VjcmV0" },
      ],
    });
    expect(info).toEqual({
      available: true,
      offerUrl: "https://voice.example.com/voice/s1/offer",
      iceServers: [
        { urls: ["stun:stun.example.com:3478"] },
        { urls: ["turn:turn.example.com:3478"], username: "1700000000:s1", credential: "c2VjcmV0" },
      ],
    });
  });

  it("is unavailable without an offer URL or when the API says so", () => {
    expect(parseVoiceInfo({ available: false, offer_url: null, ice_servers: [] }).available).toBe(false);
    expect(parseVoiceInfo({ available: true, offer_url: null, ice_servers: [] }).available).toBe(false);
    expect(parseVoiceInfo({ available: true, offer_url: "javascript:alert(1)", ice_servers: [] })).toMatchObject({
      available: false,
      offerUrl: null,
    });
    expect(parseVoiceInfo("garbage").available).toBe(false);
  });

  it("keeps the browser's echo cancellation, noise suppression and gain control on", () => {
    expect(micConstraints(null)).toEqual({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      video: false,
    });
    expect(micConstraints("mic-2").audio).toMatchObject({ deviceId: { exact: "mic-2" }, echoCancellation: true });
  });

  it("measures the microphone level", () => {
    expect(micLevel(new Uint8Array(256).fill(128))).toBe(0);
    expect(micLevel(new Uint8Array([128, 160, 128, 96]))).toBeGreaterThan(0.3);
    expect(micLevel(new Uint8Array([0, 255, 0, 255]))).toBe(1);
    expect(micLevel(new Uint8Array())).toBe(0);
    expect(deviceLabel({ label: "", deviceId: "default" }, 0)).toBe("Default microphone");
    expect(deviceLabel({ label: "", deviceId: "abc" }, 1)).toBe("Microphone 2");
  });
});

describe("relayed engine frames", () => {
  it("reads the frames the voice service forwards and ignores anything else", () => {
    expect(relayedFrame({ type: "turn.end", seq: 4, text: "Next question?", awaiting: true })).toEqual({
      type: "turn.end",
      seq: 4,
      text: "Next question?",
      awaiting: true,
    });
    expect(relayedFrame({ type: "accepted", seq: 3, client_id: "abc" })).toEqual({
      type: "accepted",
      seq: 3,
      client_id: "abc",
    });
    expect(relayedFrame({ type: "unknown" })).toBeNull();
    expect(relayedFrame("text")).toBeNull();
  });
});

describe("live captions", () => {
  const out = (overrides: Partial<BotOutput>): BotOutput => ({ text: "", ...overrides });

  it("shows spoken text and skips text announced before it is spoken", () => {
    let captions: CaptionSegment[] = [];
    captions = captionStep(
      captions,
      out({ text: "Tell me about DMA.", segment_id: 1, will_be_spoken: true, spoken_status: "new" }),
    );
    expect(captionText(captions)).toBe("");
    captions = captionStep(
      captions,
      out({
        text: "Tell",
        segment_id: 1,
        will_be_spoken: true,
        spoken_status: "in-progress",
        spoken_progress: { accumulated_text: "Tell me about", remaining_text: "DMA." },
      }),
    );
    expect(captionText(captions)).toBe("Tell me about");
    captions = captionStep(
      captions,
      out({
        text: "Tell me about DMA.",
        segment_id: 1,
        spoken: true,
        will_be_spoken: true,
        spoken_status: "completed",
      }),
    );
    expect(captionText(captions)).toBe("Tell me about DMA.");
  });

  it("keeps the last segments only", () => {
    let captions: CaptionSegment[] = [];
    for (const [id, text] of [
      [1, "One."],
      [2, "Two."],
      [3, "Three."],
    ] as const) {
      captions = captionStep(captions, out({ text, segment_id: id, spoken: true, spoken_status: "completed" }));
    }
    expect(captions).toHaveLength(CAPTION_SEGMENTS);
    expect(captionText(captions)).toBe("Two. Three.");
    expect(captionStep(captions, out({ text: "   " }))).toEqual(captions);
  });
});

describe("what went wrong", () => {
  it("explains microphone errors", () => {
    expect(micErrorMessage(new DOMException("denied", "NotAllowedError"))).toMatch(/blocked/);
    expect(micErrorMessage(new DOMException("none", "NotFoundError"))).toMatch(/No microphone/);
    expect(micErrorMessage(new DOMException("busy", "NotReadableError"))).toMatch(/in use/);
    expect(micErrorMessage(new Error("odd"))).toMatch(/could not be opened/);
  });

  it("offers text when voice cannot connect", () => {
    expect(voiceErrorMessage({ status: 403 })).toMatch(/verify/);
    expect(voiceErrorMessage({ status: 409 })).toMatch(/not available/);
    expect(voiceErrorMessage(new VoiceTimeoutError())).toMatch(/took too long/);
    expect(voiceErrorMessage(new Error("ICE failed"))).toMatch(/continue in text/);
  });
});
