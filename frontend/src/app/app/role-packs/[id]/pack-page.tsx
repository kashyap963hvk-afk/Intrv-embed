"use client";

import { Archive, ArrowLeft, Download, Share2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

import { RequireAuth } from "@/components/auth/require-auth";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { isOwner, type Me } from "@/lib/auth";
import { canArchive, canPublish, parseSpec, visibilityLabel, type RolePackDetail } from "@/lib/rolepacks";
import {
  useArchivePack,
  useExportPack,
  usePackVersions,
  usePublishPack,
  useRolePack,
  useUpdatePack,
} from "@/lib/rolepacks-api";

import { ForkDialog } from "../fork-dialog";
import { SpecEditor, type EditorTab } from "../spec-editor";

function Versions({ pack, me }: { pack: RolePackDetail; me: Me }) {
  const versions = usePackVersions(pack.id);
  if (versions.isPending) return <p className="text-sm text-muted-foreground">Loading versions…</p>;
  if (versions.isError) return <p className="text-sm text-destructive">{versions.error.message}</p>;
  return (
    <ol className="flex flex-col gap-1 text-sm">
      {versions.data.map((v) => (
        <li key={v.version} className="flex flex-wrap items-baseline gap-2">
          <span className="font-medium tabular-nums">v{v.version}</span>
          <span className="text-xs text-muted-foreground">
            {new Date(v.created_at).toLocaleString()}
            {v.created_by === me.user.id && " · by you"}
          </span>
          {v.version === pack.version && <Badge variant="secondary">current</Badge>}
        </li>
      ))}
    </ol>
  );
}

function PackHeader({ pack, owner }: { pack: RolePackDetail; owner: boolean }) {
  const publish = usePublishPack();
  const archive = useArchivePack();
  const exportPack = useExportPack();
  const router = useRouter();

  return (
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div className="flex min-w-0 flex-col gap-1">
        <Link href="/app/role-packs" className="flex w-fit items-center gap-1 text-sm text-muted-foreground">
          <ArrowLeft className="size-4" aria-hidden /> Role packs
        </Link>
        <h1 className="flex flex-wrap items-center gap-2 text-2xl font-semibold tracking-tight">
          {pack.name}
          <Badge variant="outline">{visibilityLabel(pack.visibility)}</Badge>
          <Badge variant="outline">v{pack.version}</Badge>
          {!pack.editable && <Badge variant="outline">read only</Badge>}
        </h1>
        <p className="font-mono text-xs text-muted-foreground">{pack.slug}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {pack.editable ? (
          <ForkDialog pack={pack} variant="outline" />
        ) : (
          <ForkDialog pack={pack} label="Fork to edit" variant="default" />
        )}
        {canPublish(pack, owner) && (
          <Button
            variant="outline"
            size="sm"
            disabled={publish.isPending}
            onClick={() =>
              window.confirm(`Publish ${pack.name} to the organization? Every member can then use and fork it.`) &&
              publish.mutate(pack)
            }
          >
            <Share2 /> Publish
          </Button>
        )}
        <Button variant="outline" size="sm" disabled={exportPack.isPending} onClick={() => exportPack.mutate(pack)}>
          <Download /> Export YAML
        </Button>
        {canArchive(pack) && (
          <Button
            variant="outline"
            size="sm"
            className="text-destructive"
            disabled={archive.isPending}
            onClick={() =>
              window.confirm(
                `Archive ${pack.name}? It disappears from the list; existing question sets and interviews keep working.`,
              ) && archive.mutate(pack, { onSuccess: () => router.push("/app/role-packs") })
            }
          >
            <Archive /> Archive
          </Button>
        )}
      </div>
    </div>
  );
}

function PackWorkspace({ me }: { me: Me }) {
  const { id } = useParams<{ id: string }>();
  const pack = useRolePack(id);
  const update = useUpdatePack(id);
  const [tab, setTab] = useState<EditorTab>("form");

  if (pack.isPending) return <p className="text-sm text-muted-foreground">Loading role pack…</p>;
  if (pack.isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Role pack unavailable</AlertTitle>
        <AlertDescription>
          <p>{pack.error.message}</p>
          <Link href="/app/role-packs" className={buttonVariants({ variant: "outline", size: "sm" })}>
            Back to role packs
          </Link>
        </AlertDescription>
      </Alert>
    );
  }
  const detail = pack.data;

  return (
    <section className="flex flex-col gap-6">
      <PackHeader pack={detail} owner={isOwner(me)} />
      {!detail.editable && (
        <Alert>
          <AlertTitle>Read only</AlertTitle>
          <AlertDescription>
            {detail.visibility === "SYSTEM"
              ? "Built-in packs cannot be changed. Fork it to make your own version."
              : "Only owners can change organization packs. Fork it to make your own version."}
          </AlertDescription>
        </Alert>
      )}
      <div className="grid gap-6 lg:grid-cols-[1fr_14rem]">
        <div className="min-w-0">
          <SpecEditor
            // A saved version remounts the editor with the server's content.
            key={`${detail.id}:${detail.version}`}
            initialSpec={parseSpec(detail.spec)}
            initialYaml={detail.yaml}
            readOnly={!detail.editable}
            tab={tab}
            onTabChange={setTab}
            saveLabel="Save new version"
            saving={update.isPending}
            saveError={update.error}
            onSave={(body, onSaved) => update.mutate(body, { onSuccess: onSaved })}
          />
        </div>
        <aside className="flex flex-col gap-2">
          <h2 className="text-sm font-medium">Versions</h2>
          <Versions pack={detail} me={me} />
        </aside>
      </div>
    </section>
  );
}

export function PackPage() {
  return <RequireAuth>{(me) => <PackWorkspace me={me} />}</RequireAuth>;
}
