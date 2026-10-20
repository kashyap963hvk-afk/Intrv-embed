import type { Metadata } from "next";
import { Suspense } from "react";

import { SetPage } from "./set-page";

export const metadata: Metadata = { title: "Question set" };

export default function QuestionSetPage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
      <SetPage />
    </Suspense>
  );
}
