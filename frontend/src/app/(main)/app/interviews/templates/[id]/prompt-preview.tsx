"use client";

import { Lock, PenLine } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { usePreviewPrompt } from "@/lib/interviews-api";
import { cn } from "@/lib/utils";

/** The composed interviewer prompt, layer by layer: only L2 (the persona) comes from the template. */
export function PromptPreviewPanel({ templateId, dirty }: { templateId: string; dirty: boolean }) {
  const preview = usePreviewPrompt(templateId);

  return (
    <Card>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="flex flex-col gap-1">
            <h2 className="font-semibold">Prompt preview</h2>
            <p className="text-sm text-muted-foreground">
              The interviewer&apos;s system prompt with sample values.
              {dirty && " It shows the saved template: save your changes first."}
            </p>
          </div>
          <Button size="sm" variant="outline" disabled={preview.isPending} onClick={() => preview.mutate()}>
            {preview.isPending ? "Composing…" : preview.data ? "Refresh" : "Preview prompt"}
          </Button>
        </div>
        {preview.data && (
          <div className="flex flex-col gap-3">
            {preview.data.warnings.length > 0 && (
              <ul className="list-disc space-y-0.5 pl-5 text-sm text-destructive">
                {preview.data.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            )}
            {preview.data.layers.map((layer) => (
              <section
                key={layer.layer}
                className={cn("flex flex-col gap-2 rounded-lg border p-3", !layer.editable && "bg-muted/40")}
              >
                <h3 className="flex flex-wrap items-center gap-2 text-sm font-medium">
                  <Badge variant={layer.editable ? "default" : "outline"}>{layer.layer}</Badge>
                  {layer.name}
                  {layer.editable ? (
                    <span className="flex items-center gap-1 text-xs font-normal text-muted-foreground">
                      <PenLine className="size-3" aria-hidden /> Edited in this template
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-xs font-normal text-muted-foreground">
                      <Lock className="size-3" aria-hidden /> Locked
                    </span>
                  )}
                </h3>
                <pre className="max-h-72 overflow-auto font-mono text-xs leading-relaxed whitespace-pre-wrap">
                  {layer.text}
                </pre>
              </section>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
