"use client";

import { Clock, Expand, Info, Keyboard, LoaderCircle, Mic, SendHorizontal, WifiOff } from "lucide-react";
import { useEffect, useRef, useState, type ClipboardEvent, type KeyboardEvent, type ReactNode } from "react";

import { CameraCheck } from "@/components/interview/camera-check";
import { MicSetup, VoiceStage } from "@/components/interview/voice-panel";
import { CandidatePractical, PreviewPractical, type PracticalAnswering } from "@/components/practical/practical-panel";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { useIntegrityMonitor, useInterviewRoom, useNow, type InterviewRoom } from "@/lib/interview-room";
import {
  answerMeta,
  clipboardEvent,
  elapsedSeconds,
  EMPTY_TYPING,
  formatClock,
  interviewerBusy,
  MAX_ANSWER_CHARS,
  pastedChars,
  trackInput,
  trackKey,
  trackPaste,
  type AnswerMeta,
  type BrowserEvent,
  type LiveState,
  type RoomTurn,
} from "@/lib/interviews";
import { isPracticalTopic, type PracticalTask } from "@/lib/practical";
import { postSnapshot, useVoiceInfo } from "@/lib/practical-api";
import { cn } from "@/lib/utils";
import { useMicCheck, useVoiceCall } from "@/lib/voice-session";

export interface InterviewRoomProps {
  sessionId: string;
  /** The candidate's invite token; null opens the room as the interview's creator (cookie auth). */
  token: string | null;
  interviewerName: string;
  /** What the interview is for (the role), shown in the header. */
  heading: string;
  /** Browser integrity monitoring: the exact list shown to the candidate, or null when nothing is collected. */
  monitoring: { enabled: boolean; items: string[] } | null;
  /** HTTP delivery of integrity signals while the socket is down (candidate only). */
  deliverFallback?: (events: BrowserEvent[]) => Promise<boolean>;
  /** Extra content for the ended screen (e.g. links for the owner). */
  endedExtra?: ReactNode;
  preview?: boolean;
  /** The candidate's link: voice, practical tasks and the camera check (absent in the owner's preview). */
  candidate?: CandidateSetup;
  /** The owner's preview: the practical task shown for a plan topic (by its 1-based number). */
  previewTask?: (topicNumber: number) => PracticalTask | null;
}

export interface CandidateSetup {
  token: string;
  /** The mode the interview was set up in (TEXT or VOICE); voice is offered whenever it is available. */
  sessionMode: string;
  /** The consented in-browser camera check is on for this interview. */
  camera: boolean;
  /** STRICT mode with camera consent: low-resolution snapshots for the human reviewer. */
  snapshots: boolean;
}

/** How the candidate answers: typing, setting up the microphone, or speaking. */
type Channel = "text" | "voice-setup" | "voice";

function MonitoringNote({ items }: { items: string[] }) {
  return (
    <details className="rounded-md border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 font-medium">
        <Info className="size-3.5 shrink-0" aria-hidden />
        Monitoring is on for this interview. Show what is recorded
      </summary>
      <ul className="mt-2 list-disc space-y-1 pl-5">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </details>
  );
}

function Bubble({ turn, interviewerName }: { turn: RoomTurn; interviewerName: string }) {
  const mine = turn.speaker === "CANDIDATE";
  return (
    <li className={cn("flex flex-col gap-1", mine ? "items-end" : "items-start")}>
      <span className="px-1 text-xs text-muted-foreground">{mine ? "You" : interviewerName}</span>
      <div
        className={cn(
          "max-w-[85%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap",
          mine ? "rounded-br-sm bg-primary text-primary-foreground" : "rounded-bl-sm bg-muted",
          turn.pending && "opacity-70",
        )}
      >
        {turn.text || (turn.streaming ? <span className="text-muted-foreground">…</span> : null)}
        {turn.streaming && turn.text && (
          <span className="ml-1 inline-block animate-pulse text-muted-foreground" aria-hidden>
            ▍
          </span>
        )}
      </div>
      {turn.pending && <span className="px-1 text-xs text-muted-foreground">Sending…</span>}
    </li>
  );
}

function AnswerBox({
  enabled,
  hint,
  returned,
  onSend,
  onPaste,
}: {
  enabled: boolean;
  hint: string;
  returned: { clientId: string; text: string } | null;
  onSend: (text: string, meta: AnswerMeta) => boolean;
  onPaste: (chars: number) => void;
}) {
  const [draft, setDraft] = useState("");
  const [returnedId, setReturnedId] = useState<string | null>(null);
  const tracker = useRef(EMPTY_TYPING);
  const paste = useRef<{ before: number; selection: number } | null>(null);
  const field = useRef<HTMLTextAreaElement>(null);

  // A refused answer comes back into the box (adjusting state while rendering, not in an effect).
  if (returned && returned.clientId !== returnedId) {
    setReturnedId(returned.clientId);
    setDraft(returned.text);
  }

  useEffect(() => {
    if (enabled) field.current?.focus();
  }, [enabled]);

  function submit() {
    const text = draft.trim();
    if (!enabled || !text) return;
    if (onSend(text, answerMeta(tracker.current, Date.now()))) {
      setDraft("");
      tracker.current = EMPTY_TYPING;
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    tracker.current = trackKey(tracker.current, Date.now());
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      submit();
    }
  }

  // Paste size is measured from the field (length before and after), never read from the clipboard.
  function onPasteEvent(event: ClipboardEvent<HTMLTextAreaElement>) {
    const el = event.currentTarget;
    paste.current = { before: el.value.length, selection: (el.selectionEnd ?? 0) - (el.selectionStart ?? 0) };
  }

  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <label htmlFor="answer" className="sr-only">
        Your answer
      </label>
      <Textarea
        id="answer"
        ref={field}
        rows={4}
        value={draft}
        maxLength={MAX_ANSWER_CHARS}
        disabled={!enabled}
        placeholder={enabled ? "Type your answer…" : hint}
        onKeyDown={onKeyDown}
        onPaste={onPasteEvent}
        onChange={(e) => {
          const value = e.target.value;
          tracker.current = trackInput(tracker.current, Date.now());
          if (paste.current) {
            const chars = pastedChars(paste.current.before, paste.current.selection, value.length);
            tracker.current = trackPaste(tracker.current, chars);
            onPaste(chars);
            paste.current = null;
          }
          setDraft(value);
        }}
      />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          {enabled ? "Press Ctrl+Enter (⌘+Enter on Mac) to send." : hint}
          {draft.length > MAX_ANSWER_CHARS * 0.9 && ` ${draft.length} / ${MAX_ANSWER_CHARS} characters.`}
        </p>
        <Button type="submit" disabled={!enabled || !draft.trim()}>
          <SendHorizontal /> Send
        </Button>
      </div>
    </form>
  );
}

function EndButton({
  room,
  disabled,
  beforeEnd,
}: {
  room: InterviewRoom;
  disabled: boolean;
  /** Deliver pending integrity signals while the interview is still live. */
  beforeEnd: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline" disabled={disabled}>
          End interview
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>End the interview now?</DialogTitle>
          <DialogDescription>
            The conversation so far is kept and reviewed by the hiring team. You will not be able to continue
            afterwards.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Keep going</Button>
          </DialogClose>
          <Button
            onClick={() => {
              beforeEnd();
              room.end();
              setOpen(false);
            }}
          >
            End interview
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Progress({ live, liveAt, running }: { live: LiveState; liveAt: number | null; running: boolean }) {
  const now = useNow(running);
  const elapsed = elapsedSeconds(live, liveAt, now, running);
  return (
    <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
      <span className="flex items-center gap-1 tabular-nums" title="Elapsed / planned time">
        <Clock className="size-4" aria-hidden />
        {formatClock(elapsed)} / {formatClock(live.total_minutes * 60)}
      </span>
      {live.topic_count > 0 && live.topic_number > 0 && (
        <span className="tabular-nums">
          Topic {live.topic_number} of {live.topic_count}
        </span>
      )}
    </div>
  );
}

function Ended({ preview, extra }: { preview: boolean; extra?: ReactNode }) {
  return (
    <div className="mx-auto flex max-w-lg flex-col items-center gap-3 py-16 text-center">
      <h2 className="text-2xl font-semibold tracking-tight">Thank you for your time</h2>
      <p className="text-muted-foreground">
        {preview
          ? "The interview has ended. The report is prepared in the background and appears on the interview page."
          : "The interview has ended. The hiring team will review the conversation and get back to you. You can close this window now."}
      </p>
      {extra}
    </div>
  );
}

function Waiting({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mx-auto flex max-w-lg flex-col items-center gap-3 py-16 text-center">
      <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
      <div className="flex flex-col items-center gap-3 text-muted-foreground">{children}</div>
    </div>
  );
}

function ChannelChoice({ channel, onChange }: { channel: Channel; onChange: (channel: Channel) => void }) {
  return (
    <div role="radiogroup" aria-label="How you answer" className="flex flex-wrap justify-center gap-2">
      <Button
        role="radio"
        aria-checked={channel !== "text"}
        variant={channel !== "text" ? "default" : "outline"}
        onClick={() => onChange("voice-setup")}
      >
        <Mic aria-hidden /> Speak
      </Button>
      <Button
        role="radio"
        aria-checked={channel === "text"}
        variant={channel === "text" ? "default" : "outline"}
        onClick={() => onChange("text")}
      >
        <Keyboard aria-hidden /> Type
      </Button>
    </div>
  );
}

/**
 * The interview room: streamed interviewer turns, time and topic progress (never the plan), and the candidate's
 * way of answering. Text is always available; voice (ADR 0009) connects to the voice service when it is
 * configured, and "Switch to text" keeps the same session and transcript. Practical topics show the task
 * (ADR 0010); the consented camera check (ADR 0005) runs in the browser only.
 */
export function InterviewRoomView({
  sessionId,
  token,
  interviewerName,
  heading,
  monitoring,
  deliverFallback,
  endedExtra,
  preview = false,
  candidate,
  previewTask,
}: InterviewRoomProps) {
  const room = useInterviewRoom(sessionId, token);
  const { state, connection } = room;
  const live = state.live;
  const isLive = live?.status === "LIVE" && state.ended === null;
  const monitor = useIntegrityMonitor(Boolean(monitoring?.enabled) && isLive, (events) => {
    if (room.integrity(events)) return true;
    return deliverFallback ? deliverFallback(events) : false;
  });
  const endRef = useRef<HTMLLIElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const cameraFlushRef = useRef<(() => void) | null>(null);
  const turnCount = state.turns.length;
  const lastText = state.turns.at(-1)?.text ?? "";

  const voiceInfo = useVoiceInfo(candidate?.token ?? "", Boolean(candidate));
  const voiceAvailable = voiceInfo.data?.available === true;
  const [chosen, setChosen] = useState<Channel | null>(null);
  /** A room-level message (voice fallback, diagram review) that stays until dismissed. */
  const [voiceNotice, setVoiceNotice] = useState<string | null>(null);
  // Until the candidate chooses, a voice interview starts with the microphone check (when voice is available).
  const channel: Channel = chosen ?? (voiceAvailable && candidate?.sessionMode === "VOICE" ? "voice-setup" : "text");
  const mic = useMicCheck(channel === "voice-setup" && state.ended === null);

  const toText = () => {
    setChosen("text");
    room.setMode("TEXT");
    room.resync(); // the transcript and state again, including the turns spoken over voice
  };
  const call = useVoiceCall(
    {
      onFrame: room.ingest,
      onHeard: room.heard,
      onDropped: () => {
        setVoiceNotice(
          "The voice connection dropped, so the interview continues in text. You can switch back to voice.",
        );
        toText();
      },
    },
    audioRef,
  );

  async function startVoice() {
    if (!candidate) return;
    setVoiceNotice(null);
    // Fresh ICE servers: TURN credentials are short-lived.
    const info = (await voiceInfo.refetch()).data;
    const stream = mic.take();
    if (!info?.available || !info.offerUrl || !stream) {
      if (stream) for (const track of stream.getTracks()) track.stop();
      setVoiceNotice("Voice is not available right now. Please continue in text.");
      setChosen("text");
      return;
    }
    const connected = await call.connect({
      stream,
      offerUrl: info.offerUrl,
      iceServers: info.iceServers,
      token: candidate.token,
    });
    if (connected) setChosen("voice");
    else mic.retry(); // the check opens the microphone again for another try
  }

  async function switchToText() {
    await call.disconnect();
    toText();
  }

  function startText() {
    // An interview set up for voice that runs in text is recorded as text.
    if (candidate?.sessionMode === "VOICE") room.setMode("TEXT");
    room.start();
  }

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [turnCount, lastText]);

  const ended = state.ended !== null;
  const { disconnect } = call;
  useEffect(() => {
    if (ended) void disconnect();
  }, [ended, disconnect]);

  const audio = <audio ref={audioRef} autoPlay className="hidden" />;

  if (room.closedReason) {
    return (
      <Alert className="mx-auto max-w-lg">
        <AlertTitle>We could not open this interview</AlertTitle>
        <AlertDescription>
          <p>{room.closedReason}</p>
          <p>If you think this is a mistake, please contact the person who sent you the link.</p>
        </AlertDescription>
      </Alert>
    );
  }
  if (ended) return <Ended preview={preview} extra={endedExtra} />;
  if (!live) {
    return (
      <Waiting title={connection === "reconnecting" ? "Reconnecting…" : "Connecting…"}>
        <LoaderCircle className="size-5 animate-spin" aria-hidden />
      </Waiting>
    );
  }
  if (live.status === "PREPARING") {
    return (
      <Waiting title="Almost ready">
        <p>The interview is still being prepared. Please try again in a few minutes.</p>
      </Waiting>
    );
  }
  if (live.status === "FAILED") {
    return (
      <Waiting title="This interview is not available">
        <p>It could not be prepared. Please contact the person who sent you the link.</p>
      </Waiting>
    );
  }
  if (live.status === "PLANNED") {
    const voiceWanted = channel !== "text";
    return (
      <Waiting title={preview ? "Start the preview" : "Ready when you are"}>
        {audio}
        <p>
          {interviewerName} will ask one question at a time. {voiceWanted ? "Answer out loud" : "Type your answers"};
          take your time and ask if anything is unclear.
        </p>
        {preview && (
          <p className="text-sm">
            Starting it yourself runs the real interview: the candidate cannot take it afterwards.
          </p>
        )}
        {candidate && voiceAvailable && (
          <ChannelChoice
            channel={channel}
            onChange={(next) => {
              setVoiceNotice(null);
              setChosen(next);
            }}
          />
        )}
        {candidate?.sessionMode === "VOICE" && voiceInfo.isFetched && !voiceAvailable && (
          <p className="text-sm">Voice is not available right now, so this interview runs in text.</p>
        )}
        {voiceNotice && <p className="text-sm text-destructive">{voiceNotice}</p>}
        {state.notice && <p className="text-sm text-destructive">{state.notice.message}</p>}
        {voiceWanted && candidate ? (
          <MicSetup
            check={mic}
            connecting={call.status === "connecting"}
            error={call.status === "failed" ? call.error : null}
            startLabel="Start the interview"
            onStart={() => void startVoice()}
            onUseText={() => setChosen("text")}
          />
        ) : (
          <Button size="lg" disabled={connection !== "open"} onClick={startText}>
            {connection === "open" ? "Start the interview" : "Connecting…"}
          </Button>
        )}
      </Waiting>
    );
  }

  const busy = interviewerBusy(state);
  const open = connection === "open";
  const canAnswer = open && isLive && state.awaiting && !busy;
  const hint = !open
    ? "Reconnecting… your answer box comes back in a moment."
    : busy
      ? `${interviewerName} is preparing the next question…`
      : "Please wait for the next question.";
  const onPaste = (chars: number) => monitor.record(clipboardEvent("paste", chars));
  const answering: PracticalAnswering = { send: room.answer, canAnswer, hint, onPaste, onNotice: setVoiceNotice };
  const practical = isPracticalTopic(live);
  const speaking = channel === "voice" && call.status === "connected";

  return (
    <div className="flex flex-col gap-4">
      {audio}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <h1 className="flex flex-wrap items-center gap-2 text-lg font-semibold tracking-tight">
            {heading || "Interview"}
            <Badge variant="secondary">AI interviewer: {interviewerName}</Badge>
            {preview && <Badge variant="outline">Preview</Badge>}
          </h1>
          <Progress live={live} liveAt={state.liveAt} running={isLive && open} />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {monitor.fullscreenSupported && !monitor.fullscreen && (
            <Button size="sm" variant="ghost" onClick={monitor.enterFullscreen}>
              <Expand /> Enter full screen
            </Button>
          )}
          {candidate && voiceAvailable && channel === "text" && (
            <Button
              size="sm"
              variant="outline"
              disabled={!isLive}
              onClick={() => {
                setVoiceNotice(null);
                setChosen("voice-setup");
              }}
            >
              <Mic aria-hidden /> Switch to voice
            </Button>
          )}
          <EndButton
            room={room}
            disabled={!open || !isLive}
            beforeEnd={() => {
              cameraFlushRef.current?.();
              monitor.flushNow();
            }}
          />
        </div>
      </div>

      {monitoring?.enabled && <MonitoringNote items={monitoring.items} />}
      {candidate?.camera && isLive && (
        <CameraCheck
          flushRef={cameraFlushRef}
          record={monitor.record}
          snapshot={candidate.snapshots ? (jpeg) => postSnapshot(candidate.token, jpeg) : null}
        />
      )}
      {preview && (
        <p className="text-xs text-muted-foreground">
          Preview as the interview&apos;s creator: browser integrity signals are not collected.
        </p>
      )}

      {connection === "reconnecting" && (
        <div
          role="status"
          className="flex items-center gap-2 rounded-md border border-dashed px-3 py-2 text-sm text-muted-foreground"
        >
          <WifiOff className="size-4" aria-hidden /> Reconnecting… Your progress is saved.
        </div>
      )}
      {voiceNotice && (
        <div role="status" className="flex items-center justify-between gap-2 rounded-md bg-muted px-3 py-2 text-sm">
          <span>{voiceNotice}</span>
          <Button size="sm" variant="ghost" onClick={() => setVoiceNotice(null)}>
            Dismiss
          </Button>
        </div>
      )}
      {state.notice && state.notice.code !== "busy" && (
        <div role="status" className="flex items-center justify-between gap-2 rounded-md bg-muted px-3 py-2 text-sm">
          <span>{state.notice.message}</span>
          <Button size="sm" variant="ghost" onClick={room.clearNotice}>
            Dismiss
          </Button>
        </div>
      )}

      {practical && candidate && (
        <CandidatePractical
          token={candidate.token}
          topic={live.topic_number}
          answering={answering}
          voice={speaking ? { onSwitchToText: () => void switchToText() } : null}
        />
      )}
      {practical && !candidate && previewTask && <PreviewPractical task={previewTask(live.topic_number)} />}

      <ol
        aria-live="polite"
        className="flex max-h-[60vh] min-h-64 flex-col gap-4 overflow-y-auto rounded-lg border p-4"
      >
        {state.turns.map((turn) => (
          <Bubble key={turn.key} turn={turn} interviewerName={interviewerName} />
        ))}
        {busy && !state.turns.some((t) => t.streaming) && (
          <li className="flex items-center gap-2 text-sm text-muted-foreground">
            <LoaderCircle className="size-4 animate-spin" aria-hidden /> {interviewerName} is thinking…
          </li>
        )}
        <li ref={endRef} aria-hidden className="h-px" />
      </ol>

      {speaking ? (
        <VoiceStage call={call} interviewerName={interviewerName} onSwitchToText={() => void switchToText()} />
      ) : channel === "voice-setup" && candidate ? (
        <div className="flex justify-center">
          <MicSetup
            check={mic}
            connecting={call.status === "connecting"}
            error={call.status === "failed" ? call.error : null}
            startLabel="Continue by voice"
            onStart={() => void startVoice()}
            onUseText={() => setChosen("text")}
          />
        </div>
      ) : (
        <AnswerBox enabled={canAnswer} hint={hint} returned={state.returned} onSend={room.answer} onPaste={onPaste} />
      )}
    </div>
  );
}
