"use client";

import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, Pencil, Pin, PinOff, RefreshCw, Trash } from "lucide-react";
import { useState } from "react";

import { ListField } from "@/components/list-field";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Textarea } from "@/components/ui/textarea";
import {
  categoryLabel,
  competencyName,
  difficultyLabel,
  resolveEvidence,
  type QuestionData,
  type QuestionItem,
} from "@/lib/questions";
import { useDeleteItem, useRegenerateItem, useUpdateItem } from "@/lib/questions-api";
import { cn } from "@/lib/utils";

const MIN_TEXT = 10;
const MAX_TEXT = 1200;

function ListBlock({ title, items }: { title: string; items: readonly string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="flex flex-col gap-1">
      <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{title}</p>
      <ul className="list-disc pl-5">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function RegeneratePopover({ setId, item }: { setId: string; item: QuestionItem }) {
  const [open, setOpen] = useState(false);
  const [hint, setHint] = useState("");
  const regenerate = useRegenerateItem(setId);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          size="icon"
          variant="ghost"
          aria-label="Regenerate question"
          title={item.pinned ? "Unpin to regenerate" : "Regenerate"}
          disabled={item.pinned || regenerate.isPending}
        >
          <RefreshCw className={cn(regenerate.isPending && "animate-spin")} />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80">
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            regenerate.mutate(
              { itemId: item.id, hint: hint.trim() || null },
              {
                onSuccess: () => {
                  setOpen(false);
                  setHint("");
                },
              },
            );
          }}
        >
          <div className="flex flex-col gap-2">
            <Label htmlFor={`hint-${item.id}`}>What should change? (optional)</Label>
            <Input
              id={`hint-${item.id}`}
              value={hint}
              maxLength={500}
              placeholder="More about DMA, less theory…"
              onChange={(e) => setHint(e.target.value)}
            />
          </div>
          <Button type="submit" size="sm" disabled={regenerate.isPending}>
            {regenerate.isPending ? "Regenerating…" : "Regenerate"}
          </Button>
        </form>
      </PopoverContent>
    </Popover>
  );
}

function DetailsEditor({
  setId,
  item,
  data,
  onDone,
}: {
  setId: string;
  item: QuestionItem;
  data: QuestionData;
  onDone: () => void;
}) {
  const [signals, setSignals] = useState(data.expected_signals);
  const [redFlags, setRedFlags] = useState(data.red_flags);
  const [followUps, setFollowUps] = useState(data.follow_ups);
  const [answer, setAnswer] = useState(data.ideal_answer);
  const update = useUpdateItem(setId);
  const id = `edit-${item.id}`;
  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        update.mutate(
          {
            itemId: item.id,
            update: { expected_signals: signals, red_flags: redFlags, follow_ups: followUps, ideal_answer: answer },
          },
          { onSuccess: onDone },
        );
      }}
    >
      <div className="grid gap-3 md:grid-cols-3">
        <div className="flex flex-col gap-2">
          <Label htmlFor={`${id}-signals`}>Expected signals (one per line)</Label>
          <ListField id={`${id}-signals`} value={signals} onChange={setSignals} rows={4} />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor={`${id}-flags`}>Red flags (one per line)</Label>
          <ListField id={`${id}-flags`} value={redFlags} onChange={setRedFlags} rows={4} />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor={`${id}-follow-ups`}>Follow-ups (one per line)</Label>
          <ListField id={`${id}-follow-ups`} value={followUps} onChange={setFollowUps} rows={4} />
        </div>
      </div>
      <div className="flex flex-col gap-2">
        <Label htmlFor={`${id}-answer`}>Model answer</Label>
        <Textarea
          id={`${id}-answer`}
          rows={4}
          maxLength={1500}
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
        />
      </div>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onDone}>
          Cancel
        </Button>
        <Button
          type="submit"
          size="sm"
          disabled={update.isPending || signals.length > 20 || redFlags.length > 20 || followUps.length > 8}
        >
          {update.isPending ? "Saving…" : "Save details"}
        </Button>
      </div>
    </form>
  );
}

function Details({
  setId,
  item,
  data,
  evidenceIndex,
  citationTitles,
}: {
  setId: string;
  item: QuestionItem;
  data: QuestionData;
  evidenceIndex: Readonly<Record<string, string>> | null;
  citationTitles: Readonly<Record<string, string>>;
}) {
  const [editing, setEditing] = useState(false);
  const evidence = resolveEvidence(data.evidence_refs, evidenceIndex);
  return (
    <div className="flex flex-col gap-3 border-t pt-3 text-sm">
      {data.rationale && (
        <div className="flex flex-col gap-1">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Why this question</p>
          <p>{data.rationale}</p>
        </div>
      )}
      {evidence.length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">CV evidence</p>
          <ul className="flex flex-col gap-1">
            {evidence.map(({ ref, text }) => (
              <li key={ref} className="rounded-md bg-muted/50 px-3 py-1.5">
                {text ?? <span className="text-muted-foreground">Not in the current CV profile</span>}
                <span className="ml-2 font-mono text-xs text-muted-foreground">{ref}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {editing ? (
        <DetailsEditor setId={setId} item={item} data={data} onDone={() => setEditing(false)} />
      ) : (
        <>
          <div className="grid gap-3 md:grid-cols-3">
            <ListBlock title="Expected signals" items={data.expected_signals} />
            <ListBlock title="Red flags" items={data.red_flags} />
            <ListBlock title="Follow-ups" items={data.follow_ups} />
          </div>
          {data.ideal_answer && (
            <div className="flex flex-col gap-1">
              <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                Model answer (interviewer only)
              </p>
              <p className="whitespace-pre-wrap">{data.ideal_answer}</p>
            </div>
          )}
          <div className="flex justify-end">
            <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
              <Pencil /> Edit details
            </Button>
          </div>
        </>
      )}
      {data.citations.length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Sources</p>
          <div className="flex flex-wrap gap-1">
            {data.citations.map((docId) => (
              <Badge key={docId} variant="outline" title={docId}>
                {citationTitles[docId] ?? "Deleted or unavailable document"}
              </Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function QuestionCard({
  setId,
  item,
  data,
  position,
  total,
  competencyNames,
  citationTitles,
  evidenceIndex,
  onMove,
  moving,
}: {
  setId: string;
  item: QuestionItem;
  data: QuestionData;
  position: number;
  total: number;
  competencyNames: Readonly<Record<string, string>>;
  citationTitles: Readonly<Record<string, string>>;
  evidenceIndex: Readonly<Record<string, string>> | null;
  onMove: (delta: -1 | 1) => void;
  moving: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(data.text);
  const update = useUpdateItem(setId);
  const remove = useDeleteItem(setId);
  const trimmed = draft.trim();

  return (
    <li className={cn("flex flex-col gap-3 rounded-lg border p-4", item.pinned && "border-primary/50 bg-accent/30")}>
      <div className="flex gap-3">
        <span className="w-6 shrink-0 pt-0.5 text-right text-sm font-semibold text-muted-foreground tabular-nums">
          {position + 1}.
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          {editing ? (
            <form
              className="flex flex-col gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                update.mutate({ itemId: item.id, update: { text: trimmed } }, { onSuccess: () => setEditing(false) });
              }}
            >
              <Textarea
                aria-label={`Question ${position + 1}`}
                rows={3}
                maxLength={MAX_TEXT}
                value={draft}
                autoFocus
                onChange={(e) => setDraft(e.target.value)}
              />
              <div className="flex justify-end gap-2">
                <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)}>
                  Cancel
                </Button>
                <Button type="submit" size="sm" disabled={trimmed.length < MIN_TEXT || update.isPending}>
                  {update.isPending ? "Saving…" : "Save"}
                </Button>
              </div>
            </form>
          ) : (
            <button
              type="button"
              className="text-left leading-relaxed hover:underline hover:decoration-dotted"
              title="Click to edit"
              onClick={() => {
                setDraft(data.text);
                setEditing(true);
              }}
            >
              {data.text}
            </button>
          )}
          <div className="flex flex-wrap items-center gap-1">
            {item.pinned && (
              <Badge>
                <Pin aria-hidden /> pinned
              </Badge>
            )}
            <Badge variant="secondary">{competencyName(data.competency, competencyNames)}</Badge>
            <Badge variant="outline">{categoryLabel(data.category)}</Badge>
            {data.source === "role_pack" && <Badge variant="outline">from the role pack</Badge>}
            {data.edited && <Badge variant="outline">edited</Badge>}
            {data.regenerated && <Badge variant="outline">regenerated</Badge>}
            <NativeSelect
              aria-label={`Difficulty of question ${position + 1}`}
              className="h-7 px-2 text-xs"
              value={data.difficulty}
              disabled={update.isPending}
              onChange={(e) => update.mutate({ itemId: item.id, update: { difficulty: Number(e.target.value) } })}
            >
              {[1, 2, 3, 4, 5].map((d) => (
                <option key={d} value={d}>
                  Difficulty {d}: {difficultyLabel(d)}
                </option>
              ))}
            </NativeSelect>
            <Button
              size="sm"
              variant="ghost"
              className="h-7"
              aria-expanded={expanded}
              onClick={() => setExpanded((v) => !v)}
            >
              {expanded ? <ChevronDown /> : <ChevronRight />} Details
            </Button>
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1 sm:flex-row sm:items-start">
          <Button
            size="icon"
            variant="ghost"
            aria-label={item.pinned ? "Unpin question" : "Pin question"}
            aria-pressed={item.pinned}
            title={item.pinned ? "Pinned: kept when regenerating. Click to unpin." : "Pin (protect from regeneration)"}
            disabled={update.isPending}
            onClick={() => update.mutate({ itemId: item.id, update: { pinned: !item.pinned } })}
          >
            {item.pinned ? <PinOff /> : <Pin />}
          </Button>
          <Button
            size="icon"
            variant="ghost"
            aria-label="Move up"
            title="Move up"
            disabled={position === 0 || moving}
            onClick={() => onMove(-1)}
          >
            <ArrowUp />
          </Button>
          <Button
            size="icon"
            variant="ghost"
            aria-label="Move down"
            title="Move down"
            disabled={position === total - 1 || moving}
            onClick={() => onMove(1)}
          >
            <ArrowDown />
          </Button>
          <RegeneratePopover setId={setId} item={item} />
          <Button
            size="icon"
            variant="ghost"
            className="text-destructive"
            aria-label="Delete question"
            title="Delete"
            disabled={remove.isPending}
            onClick={() => window.confirm(`Delete question ${position + 1}?`) && remove.mutate(item.id)}
          >
            <Trash />
          </Button>
        </div>
      </div>
      {expanded && (
        <Details setId={setId} item={item} data={data} evidenceIndex={evidenceIndex} citationTitles={citationTitles} />
      )}
    </li>
  );
}
