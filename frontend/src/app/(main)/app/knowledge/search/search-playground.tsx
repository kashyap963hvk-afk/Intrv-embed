"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { RequireAuth } from "@/components/auth/require-auth";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { DOC_TYPES, docTypeLabel, formatPages, highlightSegments, type DocType } from "@/lib/knowledge";
import { useSearch, useSpaces, type SearchHit } from "@/lib/knowledge-api";

function toggle<T>(set: ReadonlySet<T>, value: T, on: boolean): Set<T> {
  const next = new Set(set);
  if (on) next.add(value);
  else next.delete(value);
  return next;
}

function ResultCard({ hit, position }: { hit: SearchHit; position: number }) {
  const { citation } = hit;
  const pages = formatPages(citation.page_from, citation.page_to);
  return (
    <Card className="gap-3 py-4">
      <CardHeader className="px-4">
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          <span className="text-muted-foreground">{position}.</span>
          {citation.title}
          <Badge variant="secondary">{docTypeLabel(hit.doc_type)}</Badge>
          {pages && <Badge variant="outline">{pages}</Badge>}
          <Badge variant="outline" title="Rank after fusing keyword and semantic results">
            fused #{hit.fused_rank + 1}
          </Badge>
          {hit.rerank_score != null && (
            <Badge variant="outline" title="Reranker relevance score">
              score {hit.rerank_score.toFixed(3)}
            </Badge>
          )}
        </CardTitle>
        {citation.section_path.length > 0 && (
          <CardDescription className="text-xs">{citation.section_path.join(" > ")}</CardDescription>
        )}
      </CardHeader>
      <CardContent className="px-4">
        <p className="max-h-80 overflow-y-auto rounded-md bg-muted p-3 font-mono text-xs whitespace-pre-wrap">
          {highlightSegments(hit.text, hit.matched_text).map((segment, i) =>
            segment.match ? (
              <mark key={i} className="rounded-xs bg-yellow-200 text-foreground dark:bg-yellow-500/40">
                {segment.text}
              </mark>
            ) : (
              <span key={i}>{segment.text}</span>
            ),
          )}
        </p>
      </CardContent>
    </Card>
  );
}

function Playground() {
  const spaces = useSpaces();
  const search = useSearch();
  const [query, setQuery] = useState("");
  // Spaces left out of the search; everything readable is searched by default.
  const [excluded, setExcluded] = useState<ReadonlySet<string>>(new Set());
  const [docTypes, setDocTypes] = useState<ReadonlySet<DocType>>(new Set());
  const [k, setK] = useState(6);
  const [rerank, setRerank] = useState(true);

  const all = spaces.data ?? [];
  const chosen = all.filter((s) => !excluded.has(s.id));

  return (
    <section className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <Link href="/app/knowledge" className={buttonVariants({ variant: "link", className: "w-fit px-0" })}>
          <ArrowLeft /> Knowledge
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">Search playground</h1>
        <p className="max-w-2xl text-sm text-muted-foreground">
          See exactly what the interviewer would retrieve: hybrid keyword and semantic search over the spaces you can
          read, with the matched passage highlighted inside its section.
        </p>
      </div>

      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          search.mutate({
            query: query.trim(),
            space_ids: excluded.size === 0 ? null : chosen.map((s) => s.id),
            doc_types: docTypes.size === 0 ? null : [...docTypes],
            k,
            rerank,
          });
        }}
      >
        <div className="flex gap-2">
          <Input
            aria-label="Query"
            placeholder="e.g. TIM2_CCR1 reset value, or how does the candidate handle interrupt latency?"
            value={query}
            maxLength={2000}
            onChange={(e) => setQuery(e.target.value)}
          />
          <Button type="submit" disabled={!query.trim() || chosen.length === 0 || search.isPending}>
            {search.isPending ? "Searching…" : "Search"}
          </Button>
        </div>

        <div className="grid gap-4 md:grid-cols-3">
          <fieldset className="flex flex-col gap-2 rounded-md border p-3">
            <legend className="px-1 text-sm font-medium">Spaces</legend>
            {spaces.isPending && <p className="text-sm text-muted-foreground">Loading…</p>}
            {all.map((space) => (
              <div key={space.id} className="flex items-center gap-2">
                <Checkbox
                  id={`kn-s-${space.id}`}
                  checked={!excluded.has(space.id)}
                  onCheckedChange={(c) => setExcluded((set) => toggle(set, space.id, c !== true))}
                />
                <Label htmlFor={`kn-s-${space.id}`} className="font-normal">
                  {space.name}
                </Label>
              </div>
            ))}
            {all.length > 0 && chosen.length === 0 && (
              <p className="text-xs text-destructive">Choose at least one space.</p>
            )}
          </fieldset>

          <fieldset className="flex flex-col gap-2 rounded-md border p-3">
            <legend className="px-1 text-sm font-medium">Document types</legend>
            <p className="text-xs text-muted-foreground">None ticked means all types.</p>
            <div className="grid grid-cols-2 gap-2">
              {DOC_TYPES.map((t) => (
                <div key={t.value} className="flex items-center gap-2">
                  <Checkbox
                    id={`kn-t-${t.value}`}
                    checked={docTypes.has(t.value)}
                    onCheckedChange={(c) => setDocTypes((set) => toggle(set, t.value, c === true))}
                  />
                  <Label htmlFor={`kn-t-${t.value}`} className="font-normal">
                    {t.label}
                  </Label>
                </div>
              ))}
            </div>
          </fieldset>

          <fieldset className="flex flex-col gap-3 rounded-md border p-3">
            <legend className="px-1 text-sm font-medium">Ranking</legend>
            <div className="flex items-center justify-between gap-4">
              <Label htmlFor="kn-k">Results (k)</Label>
              <Input
                id="kn-k"
                type="number"
                min={1}
                max={20}
                className="w-20"
                value={k}
                onChange={(e) => setK(Math.min(20, Math.max(1, Number(e.target.value) || 1)))}
              />
            </div>
            <div className="flex items-center justify-between gap-4">
              <Label htmlFor="kn-rerank">Rerank with cross-encoder</Label>
              <Switch id="kn-rerank" checked={rerank} onCheckedChange={setRerank} />
            </div>
          </fieldset>
        </div>
      </form>

      {search.data && (
        <div className="flex flex-col gap-4" aria-live="polite">
          <p className="text-sm text-muted-foreground">
            {search.data.results.length} {search.data.results.length === 1 ? "result" : "results"} from{" "}
            {search.data.searched_spaces} {search.data.searched_spaces === 1 ? "space" : "spaces"}
            {search.data.reranked ? " · reranked" : rerank ? " · reranker unavailable, fused order shown" : ""}
          </p>
          {search.data.results.length === 0 && (
            <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
              Nothing matched. Only documents with status Ready are searchable.
            </p>
          )}
          {search.data.results.map((hit, i) => (
            <ResultCard key={`${hit.citation.document_id}-${hit.fused_rank}`} hit={hit} position={i + 1} />
          ))}
        </div>
      )}
    </section>
  );
}

export function SearchPlayground() {
  return <RequireAuth>{() => <Playground />}</RequireAuth>;
}
