"use client";

import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { api } from "@/lib/api/client";
import { env } from "@/lib/env";

const RECONNECT_DELAYS_MS = [2_000, 5_000, 15_000, 30_000];

export interface EventStreamHandlers {
  /** Handlers of the raw `data` of each SSE event type this subscriber cares about. */
  listeners: Record<string, (data: string) => void>;
  /** Called when the stream reopens after failing, to refetch whatever was missed meanwhile. */
  resync: () => void;
}

/**
 * Subscribe to `/api/events` while mounted. `build` turns the query client into listeners; pass a module-level
 * function so the stream is not reopened on every render. When the stream fails for good (for example an expired
 * session), the session is refreshed through the API client and the stream reopened, then `resync` runs.
 */
export function useEventStream(build: (queryClient: QueryClient) => EventStreamHandlers) {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    const { listeners, resync } = build(queryClient);
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let failures = 0;
    let closed = false;

    const connect = () => {
      source = new EventSource(`${env.apiBaseUrl}/api/events`, { withCredentials: true });
      for (const [type, handle] of Object.entries(listeners)) {
        source.addEventListener(type, (message: MessageEvent<string>) => handle(message.data));
      }
      source.onopen = () => {
        if (failures > 0) resync();
        failures = 0;
      };
      source.onerror = () => {
        // The browser retries transient drops itself; CLOSED means it gave up (e.g. HTTP 401).
        if (!source || source.readyState !== EventSource.CLOSED || closed) return;
        source.close();
        const delay = RECONNECT_DELAYS_MS[Math.min(failures, RECONNECT_DELAYS_MS.length - 1)];
        failures += 1;
        timer = setTimeout(() => {
          // Any authenticated call refreshes an expired session before the stream reconnects.
          void api.GET("/api/auth/me").finally(() => {
            if (!closed) connect();
          });
        }, delay);
      };
    };

    connect();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      source?.close();
    };
  }, [queryClient, build]);
}
