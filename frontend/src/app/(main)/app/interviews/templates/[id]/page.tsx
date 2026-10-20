import type { Metadata } from "next";
import { Suspense } from "react";

import { TemplateEditor } from "./template-editor";

export const metadata: Metadata = { title: "Interview template" };

export default function TemplatePage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
      <TemplateEditor />
    </Suspense>
  );
}
