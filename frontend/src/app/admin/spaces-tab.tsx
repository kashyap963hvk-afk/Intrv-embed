"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { useAdminUsers, useGroups } from "@/lib/admin-api";
import { grantsToRows, rowsToGrants, toPermission, type GrantRow, type OrgSpace } from "@/lib/knowledge";
import {
  useCreateOrgSpace,
  useDeleteOrgSpace,
  useOrgSpaces,
  useSetGrants,
  useUpdateOrgSpace,
} from "@/lib/knowledge-api";

import { SelfTestCard } from "./selftest-card";

interface Principals {
  users: { id: string; name: string; email: string }[];
  groups: { id: string; name: string }[];
}

let rowCounter = 0;
function newRowKey(): string {
  rowCounter += 1;
  return `new-${rowCounter}`;
}

function GrantsEditor({ space, principals }: { space: OrgSpace; principals: Principals }) {
  const [rows, setRows] = useState<GrantRow[]>(() => grantsToRows(space.grants));
  const save = useSetGrants(space.id);
  const result = rowsToGrants(rows);
  const saved = rowsToGrants(grantsToRows(space.grants));
  const dirty = JSON.stringify(result) !== JSON.stringify(saved);
  const known = new Set([
    ...principals.users.map((u) => `USER:${u.id}`),
    ...principals.groups.map((g) => `GROUP:${g.id}`),
  ]);

  function update(key: string, patch: Partial<GrantRow>) {
    setRows((current) => current.map((row) => (row.key === key ? { ...row, ...patch } : row)));
  }

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (result.ok) save.mutate(result.grants, { onSuccess: (updated) => setRows(grantsToRows(updated.grants)) });
      }}
    >
      <p className="text-sm font-medium">Who can use this space</p>
      {rows.length === 0 && (
        <p className="text-sm text-muted-foreground">Nobody yet. Owners can always manage it; members need a grant.</p>
      )}
      {rows.map((row) => (
        <div key={row.key} className="flex flex-wrap items-center gap-2">
          <NativeSelect
            aria-label="Member or group"
            className="min-w-48 flex-1"
            value={row.principal}
            onChange={(e) => update(row.key, { principal: e.target.value })}
          >
            <option value="">Choose…</option>
            {row.principal && !known.has(row.principal) && <option value={row.principal}>Former member</option>}
            <optgroup label="Groups">
              {principals.groups.map((g) => (
                <option key={g.id} value={`GROUP:${g.id}`}>
                  {g.name}
                </option>
              ))}
            </optgroup>
            <optgroup label="Members">
              {principals.users.map((u) => (
                <option key={u.id} value={`USER:${u.id}`}>
                  {u.name} ({u.email})
                </option>
              ))}
            </optgroup>
          </NativeSelect>
          <NativeSelect
            aria-label="Permission"
            value={row.permission}
            onChange={(e) => update(row.key, { permission: toPermission(e.target.value) })}
          >
            <option value="READ">Read</option>
            <option value="CONTRIBUTE">Contribute</option>
          </NativeSelect>
          <Input
            type="date"
            aria-label="Expires on (optional)"
            title="Expires on (optional)"
            className="w-40"
            value={row.expires}
            onChange={(e) => update(row.key, { expires: e.target.value })}
          />
          <Button
            type="button"
            size="sm"
            variant="ghost"
            aria-label="Remove this access"
            onClick={() => setRows((current) => current.filter((r) => r.key !== row.key))}
          >
            Remove
          </Button>
        </div>
      ))}
      {!result.ok && rows.length > 0 && <p className="text-xs text-destructive">{result.error}</p>}
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() =>
            setRows((current) => [...current, { key: newRowKey(), principal: "", permission: "READ", expires: "" }])
          }
        >
          Add access
        </Button>
        <Button type="submit" size="sm" disabled={!dirty || !result.ok || save.isPending}>
          {save.isPending ? "Saving…" : "Save access"}
        </Button>
        {dirty && (
          <Button type="button" size="sm" variant="ghost" onClick={() => setRows(grantsToRows(space.grants))}>
            Discard changes
          </Button>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        Read lets members search and cite the space; contribute also lets them add documents. Access ends at the end of
        the expiry day. Saving replaces the whole list.
      </p>
    </form>
  );
}

function OrgSpaceCard({ space, principals }: { space: OrgSpace; principals: Principals }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(space.name);
  const [description, setDescription] = useState(space.description);
  const update = useUpdateOrgSpace(space.id);
  const remove = useDeleteOrgSpace(space.id, space.name);

  return (
    <Card>
      <CardHeader>
        {editing ? (
          <form
            className="flex flex-col gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              update.mutate({ name: name.trim(), description }, { onSuccess: () => setEditing(false) });
            }}
          >
            <Input aria-label="Space name" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} />
            <Input
              aria-label="Description"
              placeholder="Description (optional)"
              value={description}
              maxLength={2000}
              onChange={(e) => setDescription(e.target.value)}
            />
            <div className="flex gap-2">
              <Button type="submit" size="sm" disabled={!name.trim() || update.isPending}>
                Save
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => {
                  setName(space.name);
                  setDescription(space.description);
                  setEditing(false);
                }}
              >
                Cancel
              </Button>
            </div>
          </form>
        ) : (
          <>
            <CardTitle className="flex flex-wrap items-center gap-2">
              {space.name}
              <Badge variant="secondary">
                {space.document_count} {space.document_count === 1 ? "document" : "documents"}
              </Badge>
            </CardTitle>
            {space.description && <CardDescription>{space.description}</CardDescription>}
          </>
        )}
        {!editing && (
          <CardAction className="flex gap-1">
            <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
              Rename
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="text-destructive"
              disabled={remove.isPending}
              onClick={() =>
                window.confirm(
                  `Delete ${space.name} and its ${space.document_count} documents? This cannot be undone.`,
                ) && remove.mutate(undefined)
              }
            >
              Delete
            </Button>
          </CardAction>
        )}
      </CardHeader>
      <CardContent>
        <GrantsEditor key={JSON.stringify(space.grants)} space={space} principals={principals} />
      </CardContent>
    </Card>
  );
}

export function SpacesTab() {
  const spaces = useOrgSpaces();
  const users = useAdminUsers("ACTIVE", "");
  const groups = useGroups();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const create = useCreateOrgSpace();
  const principals: Principals = {
    users: (users.data ?? []).map((u) => ({ id: u.id, name: u.name, email: u.email })),
    groups: (groups.data ?? []).map((g) => ({ id: g.id, name: g.name })),
  };

  return (
    <div className="flex flex-col gap-4">
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate(
            { name: name.trim(), description },
            {
              onSuccess: () => {
                setName("");
                setDescription("");
              },
            },
          );
        }}
      >
        <Input
          aria-label="Space name"
          placeholder="Space name, e.g. Firmware hiring"
          className="max-w-xs"
          maxLength={120}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <Input
          aria-label="Description"
          placeholder="Description (optional)"
          className="max-w-sm"
          maxLength={2000}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <Button type="submit" disabled={!name.trim() || create.isPending}>
          Create space
        </Button>
      </form>
      <p className="text-sm text-muted-foreground">
        Organization spaces hold shared knowledge such as rubrics, job descriptions and datasheets. Members only see the
        spaces granted to them or their groups; members&apos; personal spaces stay private.
      </p>
      {spaces.isError && <p className="text-sm text-destructive">{spaces.error.message}</p>}
      {spaces.data?.length === 0 && (
        <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">No organization spaces yet.</p>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        {spaces.data?.map((space) => (
          <OrgSpaceCard key={space.id} space={space} principals={principals} />
        ))}
      </div>
      <SelfTestCard />
    </div>
  );
}
