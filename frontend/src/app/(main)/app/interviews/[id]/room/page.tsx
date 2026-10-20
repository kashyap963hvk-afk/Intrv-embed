import type { Metadata } from "next";
import { Suspense } from "react";

import { OwnerRoom } from "./owner-room";

export const metadata: Metadata = { title: "Interview room" };

export default function InterviewRoomPage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
      <OwnerRoom />
    </Suspense>
  );
}
