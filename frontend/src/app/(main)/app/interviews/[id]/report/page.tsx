import type { Metadata } from "next";
import { Suspense } from "react";

import { ReportPage } from "./report-page";

export const metadata: Metadata = { title: "Interview report" };

export default function InterviewReportPage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
      <ReportPage />
    </Suspense>
  );
}
