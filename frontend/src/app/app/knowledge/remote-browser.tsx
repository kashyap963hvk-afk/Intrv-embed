"use client";

import { ChevronRight, File, Folder, LoaderCircle } from "lucide-react";
import { Fragment, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { enterFolder, trailTo, type Crumb, type RemoteItem } from "@/lib/connectors";
import { useBrowse } from "@/lib/connectors-api";
import { formatBytes } from "@/lib/knowledge";

function Breadcrumbs({ trail, onChange }: { trail: Crumb[]; onChange: (trail: Crumb[]) => void }) {
  return (
    <nav aria-label="Folder path" className="flex min-w-0 flex-wrap items-center gap-1 text-sm">
      {trail.map((crumb, index) => {
        const last = index === trail.length - 1;
        return (
          <Fragment key={`${crumb.id ?? "root"}-${index}`}>
            {index > 0 && <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />}
            {last ? (
              <span aria-current="location" className="max-w-48 truncate font-medium">
                {crumb.name}
              </span>
            ) : (
              <button
                type="button"
                className="max-w-48 truncate text-muted-foreground hover:text-foreground hover:underline"
                onClick={() => onChange(trailTo(trail, index))}
              >
                {crumb.name}
              </button>
            )}
          </Fragment>
        );
      })}
    </nav>
  );
}

function ItemRow({
  item,
  selected,
  onToggle,
  onOpen,
}: {
  item: RemoteItem;
  selected: boolean;
  onToggle: () => void;
  onOpen: () => void;
}) {
  const name = item.name || item.id;
  return (
    <li className="flex items-center gap-3 px-3 py-1.5 text-sm hover:bg-accent/50">
      <Checkbox checked={selected} onCheckedChange={onToggle} aria-label={`Select ${name}`} />
      {item.is_folder ? (
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-2 text-left hover:underline"
          onClick={onOpen}
          title={`Open ${item.path || name}`}
        >
          <Folder className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          <span className="truncate">{name}</span>
          <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
        </button>
      ) : (
        <span className="flex min-w-0 flex-1 items-center gap-2" title={item.path || name}>
          <File className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          <span className="truncate">{name}</span>
        </span>
      )}
      {item.size != null && !item.is_folder && (
        <span className="shrink-0 text-xs text-muted-foreground">{formatBytes(item.size)}</span>
      )}
    </li>
  );
}

/** Browse a connected account folder by folder and tick files or folders to import. */
export function RemoteBrowser({
  accountId,
  trail,
  onTrailChange,
  selectedIds,
  onToggle,
  emptyHint = "This folder is empty.",
  actions,
}: {
  accountId: string;
  trail: Crumb[];
  onTrailChange: (trail: Crumb[]) => void;
  selectedIds: ReadonlySet<string>;
  onToggle: (item: RemoteItem) => void;
  emptyHint?: string;
  /** Extra controls next to the breadcrumbs, e.g. "Import the whole folder". */
  actions?: ReactNode;
}) {
  const folder = trail[trail.length - 1];
  const listing = useBrowse(accountId, folder.id);
  const items = listing.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Breadcrumbs trail={trail} onChange={onTrailChange} />
        {actions}
      </div>
      <div className="max-h-64 overflow-y-auto rounded-md border">
        {listing.isPending && (
          <p className="flex items-center gap-2 p-3 text-sm text-muted-foreground">
            <LoaderCircle className="size-4 animate-spin" aria-hidden /> Loading…
          </p>
        )}
        {listing.isError && (
          <div className="flex flex-wrap items-center gap-2 p-3 text-sm">
            <span className="text-destructive">{listing.error.message}</span>
            <Button type="button" size="sm" variant="outline" onClick={() => void listing.refetch()}>
              Retry
            </Button>
          </div>
        )}
        {listing.isSuccess && items.length === 0 && <p className="p-3 text-sm text-muted-foreground">{emptyHint}</p>}
        {items.length > 0 && (
          <ul className="divide-y" aria-label={`Contents of ${folder.name}`}>
            {items.map((item) => (
              <ItemRow
                key={item.id}
                item={item}
                selected={selectedIds.has(item.id)}
                onToggle={() => onToggle(item)}
                onOpen={() => onTrailChange(enterFolder(trail, item))}
              />
            ))}
          </ul>
        )}
        {listing.hasNextPage && (
          <div className="border-t p-2">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="w-full"
              disabled={listing.isFetchingNextPage}
              onClick={() => void listing.fetchNextPage()}
            >
              {listing.isFetchingNextPage ? "Loading…" : "Load more"}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
