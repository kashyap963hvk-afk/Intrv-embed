"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { api } from "@/lib/api/client";
import { env } from "@/lib/env";
import {
  absenceStep,
  answerMessage,
  answerToResend,
  BUSY_RETRY_MS,
  busyRetries,
  clipboardEvent,
  controlSettled,
  enqueueEvent,
  INITIAL_ROOM,
  INTEGRITY_FLUSH_MS,
  interviewSocketUrl,
  newClientId,
  NO_ABSENCE,
  parseServerFrame,
  PING_INTERVAL_MS,
  reconnectable,
  reconnectDelay,
  roomReducer,
  takeBatch,
  type AbsenceSignal,
  type AnswerMeta,
  type BrowserEvent,
  type ControlMessage,
  type RoomState,
  type ServerFrame,
} from "@/lib/interviews";

export type Connection = "connecting" | "open" | "reconnecting" | "closed";

export interface InterviewRoom {
  state: RoomState;
  connection: Connection;
  /** Why the socket closed for good (an invalid link or session), or null. */
  closedReason: string | null;
  start: () => boolean;
  /** Send an answer; false when the socket is not open. */
  answer: (text: string, meta: AnswerMeta) => boolean;
  end: () => boolean;
  /** Integrity signals over the socket; false when it is not open (the caller falls back to HTTP). */
  integrity: (events: BrowserEvent[]) => boolean;
  /** Voice ↔ text in the same session; false when the socket is not open. */
  setMode: (mode: "TEXT" | "VOICE") => boolean;
  /** Fold in an engine frame that arrived another way (relayed by the voice service). */
  ingest: (frame: ServerFrame) => void;
  /** A final transcript of what the candidate said (voice mode). */
  heard: (text: string) => void;
  /** Reconnect now, so the server sends the transcript and state again (after voice turns this socket missed). */
  resync: () => void;
  clearNotice: () => void;
}

/**
 * The interview WebSocket: connects with the invite token (candidate) or the session cookie (owner preview),
 * reconnects with exponential backoff, pings every 25 s and resends an answer the server never recorded.
 */
export function useInterviewRoom(sessionId: string, token: string | null): InterviewRoom {
  const [state, dispatch] = useReducer(roomReducer, INITIAL_ROOM);
  const [connection, setConnection] = useState<Connection>("connecting");
  const [closedReason, setClosedReason] = useState<string | null>(null);
  const stateRef = useRef(state);
  const socketRef = useRef<WebSocket | null>(null);
  /** The page closed the socket to reconnect at once (resync), not a dropped connection. */
  const resyncRef = useRef(false);
  /** Starts, ends and mode changes not yet handled: sent again if the server answers "busy". */
  const controlsRef = useRef<ControlMessage[]>([]);

  useEffect(() => {
    stateRef.current = state;
  });

  const send = useCallback((message: object): boolean => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return false;
    socket.send(JSON.stringify(message));
    return true;
  }, []);

  useEffect(() => {
    let closed = false;
    let attempt = 0;
    let refreshed = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let busyTimer: ReturnType<typeof setTimeout> | null = null;
    let pingTimer: ReturnType<typeof setInterval> | null = null;
    const url = interviewSocketUrl(env.wsBaseUrl, sessionId, token);

    const resendAfterBusy = () => {
      for (const message of busyRetries(stateRef.current.pending, controlsRef.current)) send(message);
    };

    const connect = () => {
      const socket = new WebSocket(url);
      socketRef.current = socket;

      socket.onopen = () => {
        attempt = 0;
        refreshed = false;
        setConnection("open");
        pingTimer = setInterval(() => send({ type: "ping" }), PING_INTERVAL_MS);
      };

      socket.onmessage = (message: MessageEvent<unknown>) => {
        if (typeof message.data !== "string") return;
        const frame = parseServerFrame(message.data);
        if (!frame) return;
        if (controlsRef.current.length)
          controlsRef.current = controlsRef.current.filter((c) => !controlSettled(c, frame));
        // Decide on a resend before the frame is folded in (the reducer keeps the answer only when it must go again).
        const resend =
          frame.type === "transcript" ? answerToResend(stateRef.current.pending, frame.last_client_id) : null;
        dispatch({ type: "frame", frame, now: Date.now() });
        if (resend) send(answerMessage({ ...resend, acceptedSeq: null }));
        if (frame.type === "error" && frame.code === "busy") {
          if (busyTimer) clearTimeout(busyTimer);
          busyTimer = setTimeout(resendAfterBusy, BUSY_RETRY_MS);
        }
      };

      socket.onclose = (event) => {
        if (pingTimer) clearInterval(pingTimer);
        pingTimer = null;
        if (socketRef.current === socket) socketRef.current = null;
        if (closed) return;
        if (!reconnectable(event.code)) {
          if (!token && !refreshed) {
            // The owner's access cookie may just have expired: any API call refreshes it, then try once more.
            refreshed = true;
            setConnection("reconnecting");
            void api.GET("/api/auth/me").finally(() => {
              if (!closed) connect();
            });
            return;
          }
          setConnection("closed");
          setClosedReason(event.reason || "This interview link is not valid or has expired.");
          return;
        }
        if (stateRef.current.ended) {
          setConnection("closed");
          return;
        }
        // Anything else, including 1013 (server busy, try again later), reconnects with backoff.
        setConnection("reconnecting");
        if (resyncRef.current) {
          resyncRef.current = false;
          connect();
          return;
        }
        retryTimer = setTimeout(connect, reconnectDelay(attempt));
        attempt += 1;
      };
    };

    connect();
    return () => {
      closed = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (busyTimer) clearTimeout(busyTimer);
      if (pingTimer) clearInterval(pingTimer);
      socketRef.current?.close(1000);
      socketRef.current = null;
    };
  }, [sessionId, token, send]);

  /** Send a start, end or mode change, remembered until the server handles it (resent after "busy"). */
  const control = useCallback(
    (message: ControlMessage): boolean => {
      if (!send(message)) return false;
      controlsRef.current = [...controlsRef.current.filter((c) => c.type !== message.type), message];
      return true;
    },
    [send],
  );

  const answer = useCallback(
    (text: string, meta: AnswerMeta): boolean => {
      const pending = { clientId: newClientId(crypto), text, meta, acceptedSeq: null };
      if (!send(answerMessage(pending))) return false;
      dispatch({ type: "answer.sent", answer: pending });
      return true;
    },
    [send],
  );

  return {
    state,
    connection,
    closedReason,
    start: useCallback(() => control({ type: "start" }), [control]),
    answer,
    end: useCallback(() => control({ type: "end" }), [control]),
    integrity: useCallback((events: BrowserEvent[]) => send({ type: "integrity", events }), [send]),
    setMode: useCallback((mode: "TEXT" | "VOICE") => control({ type: "mode", mode }), [control]),
    ingest: useCallback((frame: ServerFrame) => dispatch({ type: "frame", frame, now: Date.now() }), []),
    heard: useCallback((text: string) => dispatch({ type: "voice.heard", text }), []),
    resync: useCallback(() => {
      const socket = socketRef.current;
      if (!socket || socket.readyState !== WebSocket.OPEN) return;
      resyncRef.current = true;
      socket.close(1000, "resync");
    }, []),
    clearNotice: useCallback(() => dispatch({ type: "notice.clear" }), []),
  };
}

export interface IntegrityMonitor {
  /** Queue a signal (paste sizes come from the answer box). */
  record: (event: BrowserEvent) => void;
  /** Deliver queued signals now (before the interview ends: signals sent afterwards are not stored). */
  flushNow: () => void;
  fullscreen: boolean;
  fullscreenSupported: boolean;
  enterFullscreen: () => void;
}

/** Characters selected on the page or in a text field (the selection's length, never the clipboard). */
function selectionLength(): number {
  const active = document.activeElement;
  if (active instanceof HTMLTextAreaElement || active instanceof HTMLInputElement) {
    return Math.max(0, (active.selectionEnd ?? 0) - (active.selectionStart ?? 0));
  }
  return window.getSelection()?.toString().length ?? 0;
}

/**
 * Browser integrity signals while `enabled` (the interview is live and its integrity mode is not OFF): time away
 * (tab hidden, window blur), leaving full screen and copy sizes. Signals are batched (at most 50) and handed to
 * `deliver`, which returns false to keep them queued for the next flush.
 */
export function useIntegrityMonitor(
  enabled: boolean,
  deliver: (events: BrowserEvent[]) => boolean | Promise<boolean>,
): IntegrityMonitor {
  const queueRef = useRef<BrowserEvent[]>([]);
  const deliverRef = useRef(deliver);
  const enabledRef = useRef(enabled);
  const enteredRef = useRef(false);
  const flushNowRef = useRef<() => void>(() => undefined);
  const [fullscreen, setFullscreen] = useState(false);
  const [fullscreenSupported] = useState(
    () => typeof document !== "undefined" && typeof document.documentElement.requestFullscreen === "function",
  );

  useEffect(() => {
    deliverRef.current = deliver;
    enabledRef.current = enabled;
  });

  const record = useCallback((event: BrowserEvent) => {
    if (!enabledRef.current) return;
    queueRef.current = enqueueEvent(queueRef.current, event);
  }, []);

  useEffect(() => {
    if (!enabled) return;
    let absence = NO_ABSENCE;
    let flushing = false;

    const flush = async () => {
      if (flushing || queueRef.current.length === 0) return;
      flushing = true;
      try {
        while (queueRef.current.length > 0) {
          const { batch, rest } = takeBatch(queueRef.current);
          queueRef.current = rest;
          let delivered = false;
          try {
            delivered = await deliverRef.current(batch);
          } catch {
            delivered = false;
          }
          if (!delivered) {
            queueRef.current = [...batch, ...queueRef.current];
            break;
          }
        }
      } finally {
        flushing = false;
      }
    };

    flushNowRef.current = () => void flush();

    const step = (signal: AbsenceSignal) => {
      const next = absenceStep(absence, signal, Date.now());
      absence = next.state;
      if (next.event) {
        record(next.event);
        void flush();
      }
    };
    const onVisibility = () => step(document.visibilityState === "hidden" ? "hidden" : "visible");
    const onBlur = () => step("blur");
    const onFocus = () => step("focus");
    const onCopy = () => record(clipboardEvent("copy", selectionLength()));

    if (document.visibilityState === "hidden") step("hidden");
    else if (!document.hasFocus()) step("blur");
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("blur", onBlur);
    window.addEventListener("focus", onFocus);
    document.addEventListener("copy", onCopy);
    const timer = setInterval(() => void flush(), INTEGRITY_FLUSH_MS);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("blur", onBlur);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("copy", onCopy);
      clearInterval(timer);
      flushNowRef.current = () => undefined;
      void flush();
    };
  }, [enabled, record]);

  useEffect(() => {
    const onChange = () => {
      const active = document.fullscreenElement !== null;
      setFullscreen(active);
      if (!active && enteredRef.current) {
        enteredRef.current = false;
        record({ kind: "fullscreen_exit" });
      }
    };
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, [record]);

  const enterFullscreen = useCallback(() => {
    document.documentElement
      .requestFullscreen()
      .then(() => {
        enteredRef.current = true;
      })
      .catch(() => {
        enteredRef.current = false;
      });
  }, []);

  const flushNow = useCallback(() => flushNowRef.current(), []);
  return { record, flushNow, fullscreen, fullscreenSupported, enterFullscreen };
}

/** The current time, updated every `intervalMs` while `running`. */
export function useNow(running: boolean, intervalMs = 1_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [running, intervalMs]);
  return now;
}
