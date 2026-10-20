import type { Metadata } from "next";
import { Suspense } from "react";

import { PackPage } from "./pack-page";

export const metadata: Metadata = { title: "Role pack" };

export default function RolePackPage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
      <PackPage />
    </Suspense>
  );
}
