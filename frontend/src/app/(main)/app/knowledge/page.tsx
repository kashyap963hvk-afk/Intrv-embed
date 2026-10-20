import type { Metadata } from "next";
import { Suspense } from "react";

import { KnowledgeConsole } from "./knowledge-console";

export const metadata: Metadata = { title: "Knowledge" };

export default function KnowledgePage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
      <KnowledgeConsole />
    </Suspense>
  );
}
