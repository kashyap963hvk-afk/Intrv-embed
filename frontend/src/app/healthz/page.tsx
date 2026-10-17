import type { Metadata } from "next";

import { SystemStatus } from "./system-status";

export const metadata: Metadata = { title: "System status" };

export default function HealthzPage() {
  return (
    <section className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">System status</h1>
        <p className="text-sm text-muted-foreground">Live readiness of the backend and its data stores.</p>
      </div>
      <SystemStatus />
    </section>
  );
}
