import type { Metadata } from "next";
import { Suspense } from "react";

import { AcceptInvite } from "./accept-invite";

export const metadata: Metadata = { title: "Accept invitation" };

export default function InvitePage() {
  return (
    <div className="mx-auto flex w-full max-w-sm flex-col gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">Accept invitation</h1>
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
        <AcceptInvite />
      </Suspense>
    </div>
  );
}
