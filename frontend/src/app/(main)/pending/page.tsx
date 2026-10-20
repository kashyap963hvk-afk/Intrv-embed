import type { Metadata } from "next";

import { PendingStatus } from "./pending-status";

export const metadata: Metadata = { title: "Awaiting approval" };

export default function PendingPage() {
  return (
    <div className="mx-auto flex w-full max-w-md flex-col gap-4">
      <h1 className="text-2xl font-semibold tracking-tight">Awaiting approval</h1>
      <PendingStatus />
    </div>
  );
}
