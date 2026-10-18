"use client";

import { Search } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { RequireAuth } from "@/components/auth/require-auth";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { Space } from "@/lib/knowledge";
import { useCreateSpace, useDocumentEvents, useSpaces } from "@/lib/knowledge-api";
import { cn } from "@/lib/utils";

import { AddDocumentsDialog } from "./add-documents-dialog";
import { DocumentsTable } from "./documents-table";

function NewSpaceDialog({ onCreated }: { onCreated: (space: Space) => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const create = useCreateSpace();
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) {
          setName("");
          setDescription("");
        }
      }}
    >
      <DialogTrigger asChild>
        <Button size="sm" variant="outline" className="w-full">
          New space
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New personal space</DialogTitle>
          <DialogDescription>Private to you. Use separate spaces to keep, say, CVs apart from notes.</DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate(
              { name: name.trim(), description },
              {
                onSuccess: (space) => {
                  setOpen(false);
                  onCreated(space);
                },
              },
            );
          }}
        >
          <div className="flex flex-col gap-2">
            <Label htmlFor="kn-space-name">Name</Label>
            <Input id="kn-space-name" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} required />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="kn-space-description">Description (optional)</Label>
            <Textarea
              id="kn-space-description"
              rows={3}
              maxLength={2000}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button type="submit" disabled={!name.trim() || create.isPending}>
              {create.isPending ? "Creating…" : "Create space"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function SpaceButton({ space, selected, onSelect }: { space: Space; selected: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      aria-current={selected ? "true" : undefined}
      onClick={onSelect}
      className={cn(
        "flex w-full flex-col items-start gap-1 rounded-md px-3 py-2 text-left text-sm transition-colors hover:bg-accent",
        selected && "bg-accent",
      )}
    >
      <span className="w-full truncate font-medium">{space.name}</span>
      <span className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
        {space.document_count} {space.document_count === 1 ? "document" : "documents"}
        {space.kind === "ORG" &&
          (space.permission === "CONTRIBUTE" ? (
            <Badge variant="secondary">contribute</Badge>
          ) : (
            <Badge variant="outline">read only</Badge>
          ))}
      </span>
    </button>
  );
}

function SpaceGroup({
  title,
  spaces,
  selectedId,
  onSelect,
}: {
  title: string;
  spaces: Space[];
  selectedId: string | undefined;
  onSelect: (id: string) => void;
}) {
  if (spaces.length === 0) return null;
  return (
    <div className="flex flex-col gap-1">
      <h2 className="px-3 text-xs font-medium tracking-wide text-muted-foreground uppercase">{title}</h2>
      {spaces.map((space) => (
        <SpaceButton
          key={space.id}
          space={space}
          selected={space.id === selectedId}
          onSelect={() => onSelect(space.id)}
        />
      ))}
    </div>
  );
}

function KnowledgeWorkspace() {
  const params = useSearchParams();
  const router = useRouter();
  const spaces = useSpaces();
  useDocumentEvents();

  const all = spaces.data ?? [];
  const personal = all.filter((s) => s.kind === "PERSONAL");
  const shared = all.filter((s) => s.kind === "ORG");
  const requested = params.get("space");
  const selected = all.find((s) => s.id === requested) ?? personal[0] ?? all[0];

  function select(id: string) {
    router.replace(`/app/knowledge?space=${encodeURIComponent(id)}`, { scroll: false });
  }

  return (
    <section className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-semibold tracking-tight">Knowledge</h1>
          <p className="max-w-2xl text-sm text-muted-foreground">
            CVs, job descriptions, rubrics, datasheets and notes the interviewer can cite. Your personal spaces are
            private; organization spaces are shared by owners.
          </p>
        </div>
        <Link href="/app/knowledge/search" className={buttonVariants({ variant: "outline" })}>
          <Search /> Search playground
        </Link>
      </div>

      {spaces.isError && <p className="text-sm text-destructive">{spaces.error.message}</p>}
      {spaces.isPending && <p className="text-sm text-muted-foreground">Loading spaces…</p>}

      {spaces.data && (
        <div className="grid gap-6 md:grid-cols-[15rem_1fr]">
          <nav aria-label="Knowledge spaces" className="flex flex-col gap-4">
            <SpaceGroup title="Personal" spaces={personal} selectedId={selected?.id} onSelect={select} />
            <SpaceGroup title="Organization" spaces={shared} selectedId={selected?.id} onSelect={select} />
            <NewSpaceDialog onCreated={(space) => select(space.id)} />
          </nav>

          {selected ? (
            <div className="flex min-w-0 flex-col gap-4">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="flex min-w-0 flex-col gap-1">
                  <h2 className="flex flex-wrap items-center gap-2 text-lg font-semibold">
                    {selected.name}
                    <Badge variant="outline">{selected.kind === "PERSONAL" ? "private" : "organization"}</Badge>
                    {selected.kind === "ORG" && (
                      <Badge variant={selected.permission === "CONTRIBUTE" ? "secondary" : "outline"}>
                        {selected.permission === "CONTRIBUTE" ? "contribute" : "read only"}
                      </Badge>
                    )}
                  </h2>
                  {selected.description && <p className="text-sm text-muted-foreground">{selected.description}</p>}
                </div>
                {selected.permission === "CONTRIBUTE" && <AddDocumentsDialog space={selected} />}
              </div>
              <DocumentsTable space={selected} />
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No spaces available.</p>
          )}
        </div>
      )}
    </section>
  );
}

export function KnowledgeConsole() {
  return <RequireAuth>{() => <KnowledgeWorkspace />}</RequireAuth>;
}
