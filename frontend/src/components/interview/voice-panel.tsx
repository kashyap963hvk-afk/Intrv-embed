"use client";

import { Keyboard, LoaderCircle, Mic, MicOff, Volume2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";
import { cn } from "@/lib/utils";
import { deviceLabel } from "@/lib/voice";
import { useAudioLevel, type MicCheck, type VoiceCall } from "@/lib/voice-session";

export function LevelMeter({ level, label }: { level: number; label: string }) {
  const percent = Math.round(level * 100);
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percent}
      className="h-2 w-full overflow-hidden rounded-full bg-muted"
    >
      <div
        className="h-full rounded-full bg-emerald-500 transition-[width] duration-100"
        style={{ width: `${percent}%` }}
      />
    </div>
  );
}

/**
 * Before connecting: microphone permission, device choice and a level meter, so the candidate knows they are
 * heard before the interviewer speaks.
 */
export function MicSetup({
  check,
  connecting,
  error,
  startLabel,
  onStart,
  onUseText,
}: {
  check: MicCheck;
  connecting: boolean;
  /** Why the last voice attempt failed (text is offered instead). */
  error: string | null;
  startLabel: string;
  onStart: () => void;
  onUseText: () => void;
}) {
  const level = useAudioLevel(check.stream);
  return (
    <section
      aria-label="Microphone check"
      className="flex w-full max-w-md flex-col gap-4 rounded-lg border p-4 text-left"
    >
      <div className="flex flex-col gap-1">
        <h2 className="flex items-center gap-2 font-semibold">
          <Mic className="size-4" aria-hidden /> Check your microphone
        </h2>
        <p className="text-sm text-muted-foreground">
          Say a few words: the bar should move. Use headphones if you can, so the interviewer does not hear itself.
        </p>
      </div>
      {check.error ? (
        <div role="alert" className="flex flex-col gap-2 text-sm text-destructive">
          <p>{check.error}</p>
          <Button size="sm" variant="outline" className="w-fit" onClick={check.retry}>
            Try again
          </Button>
        </div>
      ) : (
        <>
          <label className="flex flex-col gap-1.5 text-sm">
            Microphone
            <NativeSelect
              value={check.deviceId ?? ""}
              onChange={(event) => check.selectDevice(event.target.value)}
              disabled={connecting || check.devices.length === 0}
            >
              <option value="">System default</option>
              {check.devices
                .filter((device) => device.deviceId && device.deviceId !== "default")
                .map((device, index) => (
                  <option key={device.deviceId} value={device.deviceId}>
                    {deviceLabel(device, index)}
                  </option>
                ))}
            </NativeSelect>
          </label>
          <LevelMeter level={level} label="Microphone level" />
        </>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button disabled={!check.stream || connecting} onClick={onStart}>
          {connecting ? <LoaderCircle className="animate-spin" aria-hidden /> : <Mic aria-hidden />}
          {connecting ? "Connecting…" : startLabel}
        </Button>
        <Button variant="outline" disabled={connecting} onClick={onUseText}>
          <Keyboard aria-hidden /> Use text instead
        </Button>
      </div>
    </section>
  );
}

/** During a voice interview: who is speaking, always-on captions, mute and the way back to text. */
export function VoiceStage({
  call,
  interviewerName,
  onSwitchToText,
}: {
  call: VoiceCall;
  interviewerName: string;
  onSwitchToText: () => void;
}) {
  const level = useAudioLevel(call.muted ? null : call.stream);
  return (
    <section aria-label="Voice interview" className="flex flex-col gap-3 rounded-lg border p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p
          className={cn(
            "flex items-center gap-2 text-sm font-medium",
            call.botSpeaking ? "text-foreground" : "text-muted-foreground",
          )}
          aria-live="polite"
        >
          <Volume2 className={cn("size-4", call.botSpeaking && "animate-pulse text-emerald-600")} aria-hidden />
          {call.botSpeaking
            ? `${interviewerName} is speaking`
            : call.userSpeaking
              ? "Listening…"
              : `${interviewerName} is listening`}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant={call.muted ? "destructive" : "outline"}
            aria-pressed={call.muted}
            onClick={() => call.setMuted(!call.muted)}
          >
            {call.muted ? <MicOff aria-hidden /> : <Mic aria-hidden />}
            {call.muted ? "Unmute" : "Mute"}
          </Button>
          <Button size="sm" variant="outline" onClick={onSwitchToText}>
            <Keyboard aria-hidden /> Switch to text
          </Button>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <span className="shrink-0 text-xs text-muted-foreground">{call.muted ? "Muted" : "Your mic"}</span>
        <LevelMeter level={level} label="Your microphone level" />
      </div>
      <div
        aria-live="polite"
        aria-label="Live captions"
        className="min-h-16 rounded-md bg-muted px-3 py-2 text-base leading-relaxed"
      >
        {call.caption ? (
          <p>
            <span className="font-medium">{interviewerName}: </span>
            {call.caption}
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">Captions appear here while {interviewerName} speaks.</p>
        )}
        {call.interim && (
          <p className="mt-1 text-sm text-muted-foreground">
            <span className="font-medium">You: </span>
            {call.interim}
          </p>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        You can interrupt at any time; {interviewerName} stops and listens. Take your time to think before answering.
      </p>
    </section>
  );
}
