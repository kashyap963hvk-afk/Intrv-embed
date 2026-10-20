"use client";

import { LoaderCircle, RefreshCw, Trash } from "lucide-react";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Switch } from "@/components/ui/switch";
import { isOwner, useMe } from "@/lib/auth";
import {
  canManageSource,
  formatRelative,
  intervalLabel,
  kindLabel,
  parseSyncResult,
  syncResultSummary,
  type Source,
  type SyncResult,
} from "@/lib/connectors";
import { useDeleteSource, useSources, useSyncSource, useUpdateSource } from "@/lib/connectors-api";
import type { Space } from "@/lib/knowledge";

import { ConnectorIcon } from "./connector-icon";
import { IntervalSelect } from "./sync-controls";

/** The current time, refreshed every `everyMs` so relative times ("5 minutes ago") stay current. */
function useNow(everyMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(timer);
  }, [everyMs]);
  return now;
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

function SourceStatusBadge({ source }: { source: Source }) {
  if (source.status === "SYNCING") {
    return (
      <Badge variant="secondary">
        <LoaderCircle className="animate-spin" aria-hidden /> Syncing
      </Badge>
    );
  }
  if (source.status === "ERROR") {
    return (
      <Popover>
        <PopoverTrigger asChild>
          <button
            type="button"
            className="rounded-md outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
            aria-label={`Sync error for ${source.name}: show details`}
            title={source.last_error ?? undefined}
          >
            <Badge variant="destructive">Error</Badge>
          </button>
        </PopoverTrigger>
        <PopoverContent className="w-96 max-w-[90vw]" align="start">
          <div className="flex flex-col gap-2 text-sm">
            <p className="font-medium">The last sync failed</p>
            <p className="break-words text-muted-foreground">{source.last_error ?? "No details were recorded."}</p>
            {source.failure_count > 1 && (
              <p className="text-xs text-muted-foreground">
                Failed {source.failure_count} times in a row; scheduled retries back off. Use Sync now after fixing the
                account.
              </p>
            )}
          </div>
        </PopoverContent>
      </Popover>
    );
  }
  return <Badge variant="outline">Idle</Badge>;
}

function ResultDetails({ result }: { result: SyncResult }) {
  if (result.skipped.length === 0 && result.failed.length === 0) return null;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button type="button" className="text-xs underline underline-offset-4 hover:text-foreground">
          Details
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-96 max-w-[90vw]" align="start">
        <div className="flex max-h-72 flex-col gap-3 overflow-y-auto text-sm">
          {result.failed.length > 0 && (
            <div className="flex flex-col gap-1">
              <p className="font-medium text-destructive">Failed ({result.failed.length})</p>
              <ul className="flex flex-col gap-1 text-xs break-words">
                {result.failed.map((line, i) => (
                  <li key={i}>{line}</li>
                ))}
              </ul>
            </div>
          )}
          {result.skipped.length > 0 && (
            <div className="flex flex-col gap-1">
              <p className="font-medium">Skipped ({result.skipped.length})</p>
              <ul className="flex flex-col gap-1 text-xs break-words text-muted-foreground">
                {result.skipped.map((line, i) => (
                  <li key={i}>{line}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}

function DeleteSourceDialog({
  source,
  open,
  onOpenChange,
}: {
  source: Source;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [deleteDocuments, setDeleteDocuments] = useState(false);
  const remove = useDeleteSource();
  const checkboxId = `delete-docs-${source.id}`;
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) setDeleteDocuments(false);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Remove {source.name}?</DialogTitle>
          <DialogDescription>
            It stops syncing. Documents it imported stay in this space unless you delete them too.
          </DialogDescription>
        </DialogHeader>
        {source.document_count > 0 && (
          <div className="flex items-center gap-2">
            <Checkbox
              id={checkboxId}
              checked={deleteDocuments}
              onCheckedChange={(checked) => setDeleteDocuments(checked === true)}
            />
            <Label htmlFor={checkboxId} className="font-normal">
              Also delete the {plural(source.document_count, "imported document")}
            </Label>
          </div>
        )}
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={remove.isPending}
            onClick={() => remove.mutate({ source, deleteDocuments }, { onSuccess: () => onOpenChange(false) })}
          >
            {remove.isPending ? "Removing…" : deleteDocuments ? "Remove and delete documents" : "Remove source"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SourceRow({ source, manage, now }: { source: Source; manage: boolean; now: number }) {
  const [confirming, setConfirming] = useState(false);
  const sync = useSyncSource();
  const update = useUpdateSource();
  const result = parseSyncResult(source.last_result);
  const lastSync = formatRelative(source.last_sync_at, now);
  const nextSync = formatRelative(source.next_sync_at, now);
  const syncing = source.status === "SYNCING";

  return (
    <li className="flex flex-col gap-3 p-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="flex min-w-0 gap-3">
        <ConnectorIcon kind={source.kind} className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <div className="flex min-w-0 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate font-medium" title={source.name}>
              {source.name}
            </span>
            <SourceStatusBadge source={source} />
            <span className="text-xs text-muted-foreground">{kindLabel(source.kind)}</span>
          </div>
          <p className="text-xs text-muted-foreground">
            {plural(source.document_count, "document")} · {lastSync ? `Last synced ${lastSync}` : "Not synced yet"}
            {" · "}
            {source.sync_enabled
              ? `${intervalLabel(source.interval_minutes)}${nextSync && !syncing ? `, next ${nextSync}` : ""}`
              : "Not kept in sync"}
          </p>
          {result && (
            <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
              <span>Last run: {syncResultSummary(result)}</span>
              <ResultDetails result={result} />
            </p>
          )}
        </div>
      </div>
      {manage && (
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <div className="flex items-center gap-2">
            <Switch
              id={`sync-${source.id}`}
              checked={source.sync_enabled}
              disabled={update.isPending}
              onCheckedChange={(checked) => update.mutate({ source, patch: { sync_enabled: checked } })}
            />
            <Label htmlFor={`sync-${source.id}`} className="font-normal">
              Keep in sync
            </Label>
          </div>
          {source.sync_enabled && (
            <IntervalSelect
              label={`Sync interval for ${source.name}`}
              value={source.interval_minutes}
              disabled={update.isPending}
              onChange={(minutes) => update.mutate({ source, patch: { interval_minutes: minutes } })}
            />
          )}
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={syncing || sync.isPending}
            onClick={() => sync.mutate(source)}
          >
            <RefreshCw /> Sync now
          </Button>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className="text-destructive"
            aria-label={`Remove ${source.name}`}
            title="Remove source"
            onClick={() => setConfirming(true)}
          >
            <Trash />
          </Button>
          <DeleteSourceDialog source={source} open={confirming} onOpenChange={setConfirming} />
        </div>
      )}
    </li>
  );
}

/** Storage folders and web pages imported into the space, with their sync schedule and last outcome. */
export function SourcesPanel({ space }: { space: Space }) {
  const sources = useSources(space.id);
  const me = useMe();
  const owner = isOwner(me.data);
  const now = useNow(30_000);

  if (sources.isPending) return null;
  if (sources.isError) return <p className="text-sm text-destructive">{sources.error.message}</p>;
  if (sources.data.length === 0) {
    if (space.permission !== "CONTRIBUTE") return null;
    return (
      <p className="text-sm text-muted-foreground">
        No connected sources. Use Add to import from Google Drive, a bucket, OneDrive, Azure or Dropbox, or to keep a
        web page in sync.
      </p>
    );
  }
  return (
    <section className="flex flex-col gap-2" aria-labelledby={`sources-${space.id}`}>
      <h3 id={`sources-${space.id}`} className="text-base font-semibold">
        Connected sources
      </h3>
      <ul className="flex flex-col divide-y rounded-lg border">
        {sources.data.map((source) => (
          <SourceRow key={source.id} source={source} manage={canManageSource(source, owner)} now={now} />
        ))}
      </ul>
    </section>
  );
}
