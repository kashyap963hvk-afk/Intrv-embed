import type { Metadata } from "next";
import { Suspense } from "react";

import { InterviewPage } from "./interview-page";

export const metadata: Metadata = { title: "Interview" };

export default function InterviewDetailPage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
      <InterviewPage />
    </Suspense>
  );
}
