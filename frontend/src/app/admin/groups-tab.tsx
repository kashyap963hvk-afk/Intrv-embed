"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import { useAdminMutation, useAdminUsers, useGroups, type Group } from "@/lib/admin-api";

function GroupCard({ group, users }: { group: Group; users: Map<string, string> }) {
  const [candidate, setCandidate] = useState("");
  const path = { group_id: group.id };
  const add = useAdminMutation(
    async (user_id: string) =>
      unwrap(
        await api.PUT("/api/admin/groups/{group_id}/members/{user_id}", { params: { path: { ...path, user_id } } }),
      ),
    "Member added",
  );
  const removeMember = useAdminMutation(
    async (user_id: string) =>
      unwrap(
        await api.DELETE("/api/admin/groups/{group_id}/members/{user_id}", { params: { path: { ...path, user_id } } }),
      ),
    "Member removed",
  );
  const removeGroup = useAdminMutation(
    async () => unwrap(await api.DELETE("/api/admin/groups/{group_id}", { params: { path } })),
    `Group ${group.name} deleted`,
  );
  const available = [...users.entries()].filter(([id]) => !group.member_ids.includes(id));

  return (
    <Card>
      <CardHeader>
        <CardTitle>{group.name}</CardTitle>
        {group.description && <CardDescription>{group.description}</CardDescription>}
        <CardAction>
          <Button
            size="sm"
            variant="ghost"
            className="text-destructive"
            onClick={() => window.confirm(`Delete group ${group.name}?`) && removeGroup.mutate(undefined)}
          >
            Delete
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-wrap gap-1">
          {group.member_ids.length === 0 && <span className="text-sm text-muted-foreground">No members yet.</span>}
          {group.member_ids.map((id) => (
            <Badge key={id} variant="secondary" className="gap-1">
              {users.get(id) ?? "unknown"}
              <button
                type="button"
                aria-label={`Remove ${users.get(id) ?? "member"}`}
                className="ml-1 text-muted-foreground hover:text-foreground"
                onClick={() => removeMember.mutate(id)}
              >
                ×
              </button>
            </Badge>
          ))}
        </div>
        <div className="flex gap-2">
          <NativeSelect aria-label="Member to add" value={candidate} onChange={(e) => setCandidate(e.target.value)}>
            <option value="">Add member…</option>
            {available.map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </NativeSelect>
          <Button
            size="sm"
            variant="outline"
            disabled={!candidate || add.isPending}
            onClick={() => add.mutate(candidate, { onSuccess: () => setCandidate("") })}
          >
            Add
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export function GroupsTab() {
  const groups = useGroups();
  const activeUsers = useAdminUsers("ACTIVE", "");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const create = useAdminMutation(
    async () => unwrap(await api.POST("/api/admin/groups", { body: { name, description } })),
    (g) => `Group ${g.name} created`,
  );
  const users = new Map((activeUsers.data ?? []).map((u) => [u.id, u.name]));

  return (
    <div className="flex flex-col gap-4">
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate(undefined, {
            onSuccess: () => {
              setName("");
              setDescription("");
            },
          });
        }}
      >
        <Input
          aria-label="Group name"
          placeholder="Group name, e.g. Firmware panel"
          className="max-w-xs"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <Input
          aria-label="Description"
          placeholder="Description (optional)"
          className="max-w-sm"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <Button type="submit" disabled={!name.trim() || create.isPending}>
          Create group
        </Button>
      </form>
      <p className="text-sm text-muted-foreground">
        Groups make access easier: grant a knowledge space to a group instead of to each member.
      </p>
      <div className="grid gap-4 md:grid-cols-2">
        {groups.data?.map((group) => (
          <GroupCard key={group.id} group={group} users={users} />
        ))}
      </div>
    </div>
  );
}
