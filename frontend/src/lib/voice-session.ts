"use client";

import type { BotOutputData, PipecatClient, UserInputData } from "@pipecat-ai/client-js";
import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

import { isVoiceLinkLost, type ServerFrame } from "@/lib/interviews";
import {
  captionStep,
  captionText,
  micConstraints,
  micErrorMessage,
  micLevel,
  relayedFrame,
  VOICE_CONNECT_TIMEOUT_MS,
  voiceErrorMessage,
  VoiceTimeoutError,
  type CaptionSegment,
} from "@/lib/voice";

/**
 * The transport's media manager, backed only by the microphone stream the candidate checked. The transport's
 * default manager loads Daily's call bundle from a third-party CDN; this one needs nothing from the network.
 * Bot audio arrives as a remote WebRTC track and plays in the room's <audio> element.
 */
class LocalMicManager {
  private micOn = true;

  constructor(private readonly stream: MediaStream) {}

  setUserAudioCallback(): void {}
  setClientOptions(): void {}
  get supportsScreenShare(): boolean {
    return false;
  }
  get mediaSupport() {
    return { mic: true, cam: false, screenShare: false };
  }
  async initialize(): Promise<void> {}
  async connect(): Promise<void> {}
  /** The stream belongs to the room (the mic check opened it), which stops it. */
  async disconnect(): Promise<void> {}
  async getAllMics(): Promise<MediaDeviceInfo[]> {
    return (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audioinput");
  }
  async getAllCams(): Promise<MediaDeviceInfo[]> {
    return [];
  }
  async getAllSpeakers(): Promise<MediaDeviceInfo[]> {
    return [];
  }
  updateMic(): void {}
  updateCam(): void {}
  updateSpeaker(): void {}
  get selectedMic(): Record<string, never> {
    return {};
  }
  get selectedCam(): Record<string, never> {
    return {};
  }
  get selectedSpeaker(): Record<string, never> {
    return {};
  }
  enableMic(enable: boolean): void {
    this.micOn = enable;
    for (const track of this.stream.getAudioTracks()) track.enabled = enable;
  }
  enableCam(): void {}
  enableScreenShare(): void {}
  get isCamEnabled(): boolean {
    return false;
  }
  get isMicEnabled(): boolean {
    return this.micOn;
  }
  get isSharingScreen(): boolean {
    return false;
  }
  tracks(): { local: { audio?: MediaStreamTrack } } {
    return { local: { audio: this.stream.getAudioTracks()[0] } };
  }
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new VoiceTimeoutError()), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        clearTimeout(timer);
        reject(error instanceof Error ? error : new Error(String(error)));
      },
    );
  });
}

// --- the microphone check -----------------------------------------------------------------------------------------

export interface MicCheck {
  stream: MediaStream | null;
  devices: MediaDeviceInfo[];
  deviceId: string | null;
  selectDevice: (deviceId: string) => void;
  error: string | null;
  /** Ask for the microphone again (after the candidate changed a browser setting). */
  retry: () => void;
  /** Hand the stream over (to the voice call): the check no longer stops it. */
  take: () => MediaStream | null;
}

/** Open the microphone while `active` (echo cancellation, noise suppression and gain control on). */
export function useMicCheck(active: boolean): MicCheck {
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const ownedRef = useRef<MediaStream | null>(null);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const open = async () => {
      try {
        if (!navigator.mediaDevices?.getUserMedia) throw new DOMException("No media devices", "NotFoundError");
        const opened = await navigator.mediaDevices.getUserMedia(micConstraints(deviceId));
        if (cancelled) {
          for (const track of opened.getTracks()) track.stop();
          return;
        }
        ownedRef.current = opened;
        setStream(opened);
        setError(null);
        const all = await navigator.mediaDevices.enumerateDevices();
        if (!cancelled) setDevices(all.filter((d) => d.kind === "audioinput"));
      } catch (caught) {
        if (!cancelled) {
          setStream(null);
          setError(micErrorMessage(caught));
        }
      }
    };
    void open();
    return () => {
      cancelled = true;
      const owned = ownedRef.current;
      ownedRef.current = null;
      if (owned) for (const track of owned.getTracks()) track.stop();
      setStream(null);
    };
  }, [active, deviceId, attempt]);

  return {
    stream,
    devices,
    deviceId,
    selectDevice: useCallback((id: string) => setDeviceId(id || null), []),
    error,
    retry: useCallback(() => setAttempt((n) => n + 1), []),
    take: useCallback(() => {
      const taken = ownedRef.current;
      ownedRef.current = null;
      return taken;
    }, []),
  };
}

/** The level (0–1) of a stream's audio, sampled ten times a second. */
export function useAudioLevel(stream: MediaStream | null): number {
  const [level, setLevel] = useState(0);
  useEffect(() => {
    if (!stream || stream.getAudioTracks().length === 0 || typeof AudioContext === "undefined") return;
    const context = new AudioContext();
    const source = context.createMediaStreamSource(stream);
    const analyser = context.createAnalyser();
    analyser.fftSize = 512;
    source.connect(analyser);
    const samples = new Uint8Array(analyser.fftSize);
    const timer = setInterval(() => {
      analyser.getByteTimeDomainData(samples);
      setLevel(micLevel(samples));
    }, 100);
    return () => {
      clearInterval(timer);
      source.disconnect();
      void context.close();
      setLevel(0);
    };
  }, [stream]);
  return level;
}

// --- the call -----------------------------------------------------------------------------------------------------

export type VoiceStatus = "idle" | "connecting" | "connected" | "failed";

export interface VoiceCall {
  status: VoiceStatus;
  error: string | null;
  botSpeaking: boolean;
  userSpeaking: boolean;
  muted: boolean;
  /** What the interviewer is saying (always-on captions). */
  caption: string;
  /** The candidate's words while they are still speaking. */
  interim: string;
  /** The microphone stream in use (for the level meter). */
  stream: MediaStream | null;
  connect: (args: {
    stream: MediaStream;
    offerUrl: string;
    iceServers: RTCIceServer[];
    token: string;
  }) => Promise<boolean>;
  disconnect: () => Promise<void>;
  setMuted: (muted: boolean) => void;
}

export interface VoiceHandlers {
  /** An engine frame relayed by the voice service. */
  onFrame: (frame: ServerFrame) => void;
  /** A final transcript of the candidate's speech. */
  onHeard: (text: string) => void;
  /** The call ended without the interview ending (the service lost its engine link or the connection dropped). */
  onDropped: () => void;
}

const CAPTION_LINGER_MS = 4_000;

/**
 * A voice call with the voice service over SmallWebRTC (Pipecat client). The voice service drives the engine;
 * the page shows its relayed frames, captions and speaking indicators. Barge-in is handled by the server.
 */
export function useVoiceCall(handlers: VoiceHandlers, audioRef: RefObject<HTMLAudioElement | null>): VoiceCall {
  const [status, setStatus] = useState<VoiceStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [botSpeaking, setBotSpeaking] = useState(false);
  const [userSpeaking, setUserSpeaking] = useState(false);
  const [muted, setMutedState] = useState(false);
  const [segments, setSegments] = useState<CaptionSegment[]>([]);
  const [interim, setInterim] = useState("");
  const [stream, setStream] = useState<MediaStream | null>(null);
  const clientRef = useRef<PipecatClient | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const handlersRef = useRef(handlers);
  const leavingRef = useRef(false);
  const clearTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    handlersRef.current = handlers;
  });

  const release = useCallback(() => {
    const current = streamRef.current;
    streamRef.current = null;
    if (current) for (const track of current.getTracks()) track.stop();
    setStream(null);
    if (audioRef.current) audioRef.current.srcObject = null;
    if (clearTimer.current) clearTimeout(clearTimer.current);
    setBotSpeaking(false);
    setUserSpeaking(false);
    setSegments([]);
    setInterim("");
  }, [audioRef]);

  const disconnect = useCallback(async () => {
    leavingRef.current = true;
    const client = clientRef.current;
    clientRef.current = null;
    try {
      await client?.disconnect();
    } finally {
      release();
      setStatus("idle");
      setMutedState(false);
    }
  }, [release]);

  const connect = useCallback(
    async ({
      stream: mic,
      offerUrl,
      iceServers,
      token,
    }: {
      stream: MediaStream;
      offerUrl: string;
      iceServers: RTCIceServer[];
      token: string;
    }): Promise<boolean> => {
      leavingRef.current = false;
      streamRef.current = mic;
      setStream(mic);
      setError(null);
      setStatus("connecting");
      try {
        const [{ PipecatClient: Client, LogLevel }, { SmallWebRTCTransport }] = await Promise.all([
          import("@pipecat-ai/client-js"),
          import("@pipecat-ai/small-webrtc-transport"),
        ]);
        const transport = new SmallWebRTCTransport({ mediaManager: new LocalMicManager(mic), iceServers });
        const client = new Client({
          transport,
          enableMic: true,
          enableCam: false,
          callbacks: {
            onServerMessage: (data: unknown) => {
              const frame = relayedFrame(data);
              if (!frame) return;
              if (isVoiceLinkLost(frame)) handlersRef.current.onDropped();
              else handlersRef.current.onFrame(frame);
            },
            onBotOutput: (data: BotOutputData) => {
              if (clearTimer.current) clearTimeout(clearTimer.current);
              setSegments((current) =>
                captionStep(current, {
                  text: data.text,
                  text_type: typeof data.text_type === "string" ? data.text_type : undefined,
                  segment_id: data.segment_id,
                  spoken: data.spoken,
                  will_be_spoken: data.will_be_spoken,
                  spoken_status: data.spoken_status,
                  spoken_progress: data.spoken_progress,
                }),
              );
            },
            onBotStartedSpeaking: () => {
              if (clearTimer.current) clearTimeout(clearTimer.current);
              setBotSpeaking(true);
            },
            onBotStoppedSpeaking: () => {
              setBotSpeaking(false);
              if (clearTimer.current) clearTimeout(clearTimer.current);
              clearTimer.current = setTimeout(() => setSegments([]), CAPTION_LINGER_MS);
            },
            onUserStartedSpeaking: () => setUserSpeaking(true),
            onUserStoppedSpeaking: () => setUserSpeaking(false),
            onUserInput: (data: UserInputData) => {
              if (data.input_type !== "transcription") return;
              if (data.final) {
                setInterim("");
                handlersRef.current.onHeard(data.text);
              } else {
                setInterim(data.text);
              }
            },
            onTrackStarted: (track: MediaStreamTrack) => {
              const element = audioRef.current;
              if (track.kind !== "audio" || !element) return;
              element.srcObject = new MediaStream([track]);
              void element.play().catch(() => undefined);
            },
            onDisconnected: () => {
              if (leavingRef.current || clientRef.current !== client) return;
              clientRef.current = null;
              release();
              setStatus("failed");
              setError("The voice connection dropped. Continue in text, or switch back to voice.");
              handlersRef.current.onDropped();
            },
          },
        });
        client.setLogLevel(LogLevel.WARN);
        clientRef.current = client;
        await withTimeout(
          client.connect({
            webrtcRequestParams: {
              endpoint: offerUrl,
              headers: new Headers({ Authorization: `Bearer ${token}` }),
              timeout: VOICE_CONNECT_TIMEOUT_MS,
            },
            iceConfig: { iceServers },
          }),
          VOICE_CONNECT_TIMEOUT_MS,
        );
        if (clientRef.current !== client) return false;
        setStatus("connected");
        return true;
      } catch (caught) {
        const client = clientRef.current;
        clientRef.current = null;
        leavingRef.current = true;
        await client?.disconnect().catch(() => undefined);
        release();
        setStatus("failed");
        setError(voiceErrorMessage(caught));
        return false;
      }
    },
    [audioRef, release],
  );

  const setMuted = useCallback((next: boolean) => {
    clientRef.current?.enableMic(!next);
    setMutedState(next);
  }, []);

  useEffect(
    () => () => {
      leavingRef.current = true;
      const client = clientRef.current;
      clientRef.current = null;
      void client?.disconnect().catch(() => undefined);
      const current = streamRef.current;
      streamRef.current = null;
      if (current) for (const track of current.getTracks()) track.stop();
    },
    [],
  );

  return {
    status,
    error,
    botSpeaking,
    userSpeaking,
    muted,
    caption: captionText(segments),
    interim,
    stream,
    connect,
    disconnect,
    setMuted,
  };
}
