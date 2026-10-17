"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import { useAdminMutation, useAdminUsers, type AdminUser, type UserStatus } from "@/lib/admin-api";

import { PolicyDialog } from "./policy-dialog";

const STATUS_VARIANT = { PENDING: "outline", ACTIVE: "success", SUSPENDED: "destructive" } as const;

function formatDate(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString() : "never";
}

function UserActions({ user, isSelf }: { user: AdminUser; isSelf: boolean }) {
  const path = { params: { path: { user_id: user.id } } };
  const approve = useAdminMutation(async () => unwrap(await api.POST("/api/admin/users/{user_id}/approve", path)), `${user.name} approved`);
  const reject = useAdminMutation(async () => unwrap(await api.POST("/api/admin/users/{user_id}/reject", path)), `Request from ${user.name} rejected`);
  const suspend = useAdminMutation(async () => unwrap(await api.POST("/api/admin/users/{user_id}/suspend", path)), `${user.name} suspended`);
  const reactivate = useAdminMutation(async () => unwrap(await api.POST("/api/admin/users/{user_id}/reactivate", path)), `${user.name} reactivated`);
  const remove = useAdminMutation(async () => unwrap(await api.DELETE("/api/admin/users/{user_id}", path)), `${user.name} deleted`);
  const setRole = useAdminMutation(
    async (role: "OWNER" | "MEMBER") =>
      unwrap(await api.PUT("/api/admin/users/{user_id}/role", { ...path, body: { role } })),
    (u) => `${u.name} is now ${u.role.toLowerCase()}`,
  );
  const busy = [approve, reject, suspend, reactivate, remove, setRole].some((m) => m.isPending);

  return (
    <div className="flex flex-wrap justify-end gap-1">
      {user.status === "PENDING" && (
        <>
          <Button size="sm" disabled={busy} onClick={() => approve.mutate(undefined)}>
            Approve
          </Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => reject.mutate(undefined)}>
            Reject
          </Button>
        </>
      )}
      {user.status === "ACTIVE" && !isSelf && (
        <Button size="sm" variant="outline" disabled={busy} onClick={() => suspend.mutate(undefined)}>
          Suspend
        </Button>
      )}
      {user.status === "SUSPENDED" && (
        <Button size="sm" variant="outline" disabled={busy} onClick={() => reactivate.mutate(undefined)}>
          Reactivate
        </Button>
      )}
      {user.status === "ACTIVE" && (
        <Button
          size="sm"
          variant="ghost"
          disabled={busy}
          onClick={() => setRole.mutate(user.role === "OWNER" ? "MEMBER" : "OWNER")}
        >
          {user.role === "OWNER" ? "Make member" : "Make owner"}
        </Button>
      )}
      {user.status !== "PENDING" && <PolicyDialog user={user} />}
      {!isSelf && user.status !== "PENDING" && (
        <Button
          size="sm"
          variant="ghost"
          className="text-destructive"
          disabled={busy}
          onClick={() => {
            if (window.confirm(`Delete ${user.name}? This cannot be undone.`)) remove.mutate(undefined);
          }}
        >
          Delete
        </Button>
      )}
    </div>
  );
}

export function UsersTab({ currentUserId }: { currentUserId: string }) {
  const [status, setStatus] = useState<UserStatus | "">("");
  const [q, setQ] = useState("");
  const users = useAdminUsers(status || undefined, q);
  const pendingCount = useAdminUsers("PENDING", "").data?.length ?? 0;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <NativeSelect aria-label="Filter by status" value={status} onChange={(e) => setStatus(e.target.value as UserStatus | "")}>
          <option value="">All members</option>
          <option value="PENDING">Pending ({pendingCount})</option>
          <option value="ACTIVE">Active</option>
          <option value="SUSPENDED">Suspended</option>
        </NativeSelect>
        <Input
          aria-label="Search members"
          placeholder="Search name or email"
          className="max-w-xs"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </div>
      {users.isError && <p className="text-sm text-destructive">{users.error.message}</p>}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Member</TableHead>
            <TableHead>Role</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Last sign-in</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {users.data?.map((user) => (
            <TableRow key={user.id}>
              <TableCell>
                <div className="font-medium">{user.name}</div>
                <div className="text-xs text-muted-foreground">{user.email}</div>
              </TableCell>
              <TableCell>{user.role.toLowerCase()}</TableCell>
              <TableCell>
                <Badge variant={STATUS_VARIANT[user.status]}>{user.status.toLowerCase()}</Badge>
              </TableCell>
              <TableCell className="text-xs text-muted-foreground">{formatDate(user.last_login_at)}</TableCell>
              <TableCell>
                <UserActions user={user} isSelf={user.id === currentUserId} />
              </TableCell>
            </TableRow>
          ))}
          {users.data?.length === 0 && (
            <TableRow>
              <TableCell colSpan={5} className="py-6 text-center text-muted-foreground">
                No members match.
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  );
}
