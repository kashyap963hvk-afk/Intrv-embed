"use client";

import { Trash } from "lucide-react";
import Link from "next/link";

import { RequireAuth } from "@/components/auth/require-auth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { isOwner, type Me } from "@/lib/auth";
import { parseSettings, type Template } from "@/lib/interviews";
import { useDeleteTemplate, useTemplates } from "@/lib/interviews-api";
import { useRolePacks } from "@/lib/rolepacks-api";

import { SectionNav } from "../section-nav";
import { NewTemplateDialog } from "./new-template-dialog";

function TemplateRow({ template, packName, canEdit }: { template: Template; packName: string; canEdit: boolean }) {
  const remove = useDeleteTemplate();
  const settings = parseSettings(template.settings);
  return (
    <TableRow>
      <TableCell className="max-w-sm">
        <div className="flex flex-col gap-0.5">
          <Link
            href={`/app/interviews/templates/${template.id}`}
            className="truncate font-medium hover:underline"
            title={template.name}
          >
            {template.name}
          </Link>
          {template.warnings.length > 0 && (
            <span className="text-xs text-destructive">
              {template.warnings.length} persona {template.warnings.length === 1 ? "warning" : "warnings"}
            </span>
          )}
        </div>
      </TableCell>
      <TableCell className="text-sm">{packName}</TableCell>
      <TableCell className="text-sm tabular-nums">{settings.duration_minutes} min</TableCell>
      <TableCell>
        {template.shared ? <Badge variant="secondary">Organization</Badge> : <Badge variant="outline">Mine</Badge>}
      </TableCell>
      <TableCell className="text-xs whitespace-nowrap text-muted-foreground">
        {new Date(template.updated_at).toLocaleString()}
      </TableCell>
      <TableCell>
        <div className="flex justify-end">
          {canEdit && (
            <Button
              size="icon"
              variant="ghost"
              className="text-destructive"
              aria-label={`Delete ${template.name}`}
              title="Delete"
              disabled={remove.isPending}
              onClick={() => window.confirm(`Delete the template ${template.name}?`) && remove.mutate(template)}
            >
              <Trash />
            </Button>
          )}
        </div>
      </TableCell>
    </TableRow>
  );
}

function Templates({ me }: { me: Me }) {
  const templates = useTemplates();
  const packs = useRolePacks();
  const owner = isOwner(me);

  return (
    <section className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-3">
          <SectionNav current="/app/interviews/templates" />
          <div className="flex flex-col gap-1">
            <h1 className="text-2xl font-semibold tracking-tight">Interview templates</h1>
            <p className="max-w-2xl text-sm text-muted-foreground">
              The interviewer&apos;s persona (prompt layer L2) and default settings, reused across interviews. The
              platform rules (L0) and your organization&apos;s policy (L1) always apply on top.
            </p>
          </div>
        </div>
        <NewTemplateDialog owner={owner} />
      </div>

      {templates.isError && <p className="text-sm text-destructive">{templates.error.message}</p>}
      {templates.isPending && <p className="text-sm text-muted-foreground">Loading templates…</p>}
      {templates.data &&
        (templates.data.length === 0 ? (
          <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
            No templates yet. Interviews can also be created without one, from a role pack.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Role pack</TableHead>
                <TableHead>Duration</TableHead>
                <TableHead>Visibility</TableHead>
                <TableHead>Updated</TableHead>
                <TableHead className="text-right">
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {templates.data.map((template) => (
                <TemplateRow
                  key={template.id}
                  template={template}
                  packName={
                    template.role_pack_id
                      ? (packs.data?.find((p) => p.id === template.role_pack_id)?.name ?? "—")
                      : "None yet"
                  }
                  canEdit={!template.shared || owner}
                />
              ))}
            </TableBody>
          </Table>
        ))}
    </section>
  );
}

export function TemplatesConsole() {
  return <RequireAuth>{(me) => <Templates me={me} />}</RequireAuth>;
}
