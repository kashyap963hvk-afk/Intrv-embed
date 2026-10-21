"use client";

import { Camera, CameraOff } from "lucide-react";
import { useEffect, useRef } from "react";

import { useCameraMonitor } from "@/lib/camera-monitor";
import type { BrowserEvent } from "@/lib/interviews";
import { cn } from "@/lib/utils";

function SelfView({ stream }: { stream: MediaStream }) {
  const video = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const element = video.current;
    if (!element) return;
    element.srcObject = stream;
    void element.play().catch(() => undefined);
    return () => {
      element.srcObject = null;
    };
  }, [stream]);
  // Mirrored like a mirror; the check itself uses the camera's own (unmirrored) frames.
  return (
    <video
      ref={video}
      muted
      playsInline
      aria-label="Your camera"
      className="h-24 w-32 shrink-0 -scale-x-100 rounded-md bg-black object-cover"
    />
  );
}

/**
 * The consented camera check (ADR 0005): a small self-view and a clear indicator. Face count and head direction
 * are computed here in the browser; only derived events (and STRICT snapshots) are sent, never the video.
 */
export function CameraCheck({
  record,
  snapshot,
}: {
  record: (event: BrowserEvent) => void;
  snapshot: ((jpeg: Blob) => Promise<boolean>) | null;
}) {
  const camera = useCameraMonitor({ enabled: true, record, snapshot });
  const on = camera.status === "on";
  return (
    <aside
      aria-label="Camera monitoring"
      className="flex items-center gap-3 rounded-md border bg-muted/40 px-3 py-2 text-xs text-muted-foreground"
    >
      {camera.stream ? (
        <SelfView stream={camera.stream} />
      ) : (
        <div className="flex h-24 w-32 shrink-0 items-center justify-center rounded-md bg-muted">
          <CameraOff className="size-5" aria-hidden />
        </div>
      )}
      <div className="flex flex-col gap-1" aria-live="polite">
        <p className={cn("flex items-center gap-1.5 font-medium", on ? "text-foreground" : undefined)}>
          <span className={cn("size-2 rounded-full", on ? "bg-red-500" : "bg-muted-foreground")} aria-hidden />
          <Camera className="size-3.5" aria-hidden />
          {on
            ? "Camera monitoring on"
            : camera.status === "starting"
              ? "Starting the camera check…"
              : "Camera check off"}
        </p>
        {camera.error ? (
          <p className="text-destructive">{camera.error}</p>
        ) : (
          <p>
            Checked on this device only: whether you are in view, alone, and facing the screen.
            {snapshot ? " A small snapshot goes to the reviewer about once a minute." : ""} No video is recorded.
          </p>
        )}
        {on && camera.faces === 0 && (
          <p className="text-amber-700 dark:text-amber-400">We can&apos;t see you right now.</p>
        )}
      </div>
    </aside>
  );
}
