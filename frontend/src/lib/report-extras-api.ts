"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import { interviewKeys } from "@/lib/interviews-api";
import { EXTRAS_REFRESH_MS, parseReportExtras, type ReportExtras } from "@/lib/report-extras";

/** While the interview runs, new submissions and snapshots arrive; poll a little more often. */
const LIVE_POLL_MS = 15_000;

/**
 * Practical work and camera snapshots for a report. A separate query under the report's key (so every report
 * invalidation refreshes it too) that also refetches before the presigned image links expire.
 */
export function useReportExtras(id: string, live: boolean) {
  return useQuery({
    queryKey: [...interviewKeys.report(id), "extras"] as const,
    queryFn: async (): Promise<ReportExtras> =>
      parseReportExtras(
        unwrap(await api.GET("/api/interviews/{session_id}/report", { params: { path: { session_id: id } } })),
      ),
    refetchInterval: live ? LIVE_POLL_MS : EXTRAS_REFRESH_MS,
    staleTime: 60_000,
    retry: false,
  });
}
