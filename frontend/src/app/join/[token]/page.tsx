import type { Metadata } from "next";
import { Suspense } from "react";

import { JoinPage } from "./join-page";

export const metadata: Metadata = { title: "Your interview", robots: { index: false, follow: false } };

/** The candidate's page: no sign-in, no app navigation; the invite token in the URL is the credential. */
export default function JoinInterviewPage() {
  return (
    <main className="mx-auto flex min-h-full w-full max-w-5xl flex-col px-4 py-8">
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
        <JoinPage />
      </Suspense>
    </main>
  );
}
