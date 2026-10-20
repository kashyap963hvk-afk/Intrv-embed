"use client";

import { Archive, Download, Share2 } from "lucide-react";
import Link from "next/link";

import { RequireAuth } from "@/components/auth/require-auth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { isOwner } from "@/lib/auth";
import { canArchive, canPublish, groupPacks, visibilityLabel, type RolePack } from "@/lib/rolepacks";
import { useArchivePack, useExportPack, usePublishPack, useRolePacks } from "@/lib/rolepacks-api";

import { BuilderDialog } from "./builder-dialog";
import { ForkDialog } from "./fork-dialog";

function PackRow({ pack, owner }: { pack: RolePack; owner: boolean }) {
  const publish = usePublishPack();
  const archive = useArchivePack();
  const exportPack = useExportPack();

  return (
    <TableRow>
      <TableCell className="max-w-md">
        <div className="flex flex-col gap-1">
          <Link href={`/app/role-packs/${pack.id}`} className="truncate font-medium hover:underline" title={pack.name}>
            {pack.name}
          </Link>
          {pack.description && (
            <span className="line-clamp-2 text-xs text-muted-foreground" title={pack.description}>
              {pack.description}
            </span>
          )}
          <span className="flex flex-wrap gap-1">
            <Badge variant="outline">{visibilityLabel(pack.visibility)}</Badge>
            <Badge variant="outline">v{pack.version}</Badge>
            {pack.forked_from_id && <Badge variant="secondary">fork</Badge>}
            {pack.editable ? <Badge variant="secondary">editable</Badge> : <Badge variant="outline">read only</Badge>}
          </span>
        </div>
      </TableCell>
      <TableCell className="text-sm whitespace-nowrap">{pack.competency_count}</TableCell>
      <TableCell className="max-w-xs text-xs text-muted-foreground">
        {pack.variants.map((v) => v.name).join(", ") || "—"}
      </TableCell>
      <TableCell>
        <div className="flex flex-wrap justify-end gap-1">
          <ForkDialog pack={pack} />
          {canPublish(pack, owner) && (
            <Button
              size="sm"
              variant="ghost"
              disabled={publish.isPending}
              title="Share with everyone in the organization"
              onClick={() =>
                window.confirm(`Publish ${pack.name} to the organization? Every member can then use and fork it.`) &&
                publish.mutate(pack)
              }
            >
              <Share2 /> Publish
            </Button>
          )}
          <Button
            size="icon"
            variant="ghost"
            aria-label={`Export ${pack.name} as YAML`}
            title="Export YAML"
            disabled={exportPack.isPending}
            onClick={() => exportPack.mutate(pack)}
          >
            <Download />
          </Button>
          {canArchive(pack) && (
            <Button
              size="icon"
              variant="ghost"
              className="text-destructive"
              aria-label={`Archive ${pack.name}`}
              title="Archive"
              disabled={archive.isPending}
              onClick={() =>
                window.confirm(
                  `Archive ${pack.name}? It disappears from the list; existing question sets and interviews keep working.`,
                ) && archive.mutate(pack)
              }
            >
              <Archive />
            </Button>
          )}
        </div>
      </TableCell>
    </TableRow>
  );
}

function PackGroup({
  title,
  help,
  packs,
  owner,
  empty,
}: {
  title: string;
  help: string;
  packs: RolePack[];
  owner: boolean;
  empty?: string;
}) {
  if (packs.length === 0 && !empty) return null;
  return (
    <section className="flex flex-col gap-2">
      <div className="flex flex-col gap-0.5">
        <h2 className="text-lg font-semibold">{title}</h2>
        <p className="text-sm text-muted-foreground">{help}</p>
      </div>
      {packs.length === 0 ? (
        <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">{empty}</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Pack</TableHead>
              <TableHead>Competencies</TableHead>
              <TableHead>Role variants</TableHead>
              <TableHead className="text-right">
                <span className="sr-only">Actions</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {packs.map((pack) => (
              <PackRow key={pack.id} pack={pack} owner={owner} />
            ))}
          </TableBody>
        </Table>
      )}
    </section>
  );
}

function RolePacksList({ owner }: { owner: boolean }) {
  const packs = useRolePacks();
  const groups = groupPacks(packs.data ?? []);

  return (
    <section className="flex flex-col gap-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-semibold tracking-tight">Role packs</h1>
          <p className="max-w-2xl text-sm text-muted-foreground">
            What good looks like for a role: competencies with anchored levels, signals and red flags, and how much each
            one counts per role variant. Fork a built-in pack to tailor it, or draft a new one with AI.
          </p>
        </div>
        <BuilderDialog packs={packs.data ?? []} />
      </div>

      {packs.isError && <p className="text-sm text-destructive">{packs.error.message}</p>}
      {packs.isPending && <p className="text-sm text-muted-foreground">Loading role packs…</p>}

      {packs.data && (
        <>
          <PackGroup
            title="Built-in"
            help="Shipped with Intrv-embed. Read only: fork one to change it."
            packs={groups.builtIn}
            owner={owner}
          />
          <PackGroup
            title="Organization"
            help={
              owner
                ? "Shared with every member. Owners can edit them."
                : "Shared with every member by the owners. Fork one to change it."
            }
            packs={groups.organization}
            owner={owner}
          />
          <PackGroup
            title="Mine"
            help={owner ? "Private to you until you publish them to the organization." : "Private to you."}
            packs={groups.mine}
            owner={owner}
            empty="No packs of your own yet. Fork a pack above or use Build with AI."
          />
        </>
      )}
    </section>
  );
}

export function RolePacksConsole() {
  return <RequireAuth>{(me) => <RolePacksList owner={isOwner(me)} />}</RequireAuth>;
}
