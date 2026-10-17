"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import { adminKeys, useAdminUsers } from "@/lib/admin-api";

export function AuditTab() {
  const [action, setAction] = useState("");
  const users = useAdminUsers(undefined, "");
  const names = new Map((users.data ?? []).map((u) => [u.id, u.name]));
  const audit = useInfiniteQuery({
    queryKey: adminKeys.audit(action),
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }) =>
      unwrap(
        await api.GET("/api/admin/audit", { params: { query: { limit: 50, cursor: pageParam, action: action || null } } }),
      ),
    getNextPageParam: (last) => last.next_cursor,
  });
  const items = audit.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <div className="flex flex-col gap-4">
      <Input
        aria-label="Filter by action"
        placeholder="Filter by action prefix, e.g. user. or auth."
        className="max-w-sm"
        value={action}
        onChange={(e) => setAction(e.target.value)}
      />
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>When</TableHead>
            <TableHead>Action</TableHead>
            <TableHead>Actor</TableHead>
            <TableHead>Target</TableHead>
            <TableHead>Details</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => (
            <TableRow key={item.id}>
              <TableCell className="text-xs whitespace-nowrap text-muted-foreground">{new Date(item.at).toLocaleString()}</TableCell>
              <TableCell className="font-mono text-xs">{item.action}</TableCell>
              <TableCell className="text-xs">{item.actor_id ? (names.get(item.actor_id) ?? item.actor_id) : "system"}</TableCell>
              <TableCell className="text-xs">
                {item.target_type}
                {item.target_id ? `: ${names.get(item.target_id) ?? item.target_id}` : ""}
              </TableCell>
              <TableCell className="max-w-xs truncate font-mono text-xs text-muted-foreground">
                {Object.keys(item.detail).length ? JSON.stringify(item.detail) : ""}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {audit.hasNextPage && (
        <Button variant="outline" className="w-fit" disabled={audit.isFetchingNextPage} onClick={() => void audit.fetchNextPage()}>
          Load more
        </Button>
      )}
    </div>
  );
}
