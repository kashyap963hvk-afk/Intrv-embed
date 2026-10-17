"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import { adminKeys, useAdminMutation } from "@/lib/admin-api";

export function OrgTab() {
  const org = useQuery({ queryKey: adminKeys.org, queryFn: async () => unwrap(await api.GET("/api/admin/org")) });
  const [name, setName] = useState<string | null>(null);
  const rename = useAdminMutation(
    async (newName: string) => unwrap(await api.PATCH("/api/admin/org", { body: { name: newName } })),
    "Organization renamed",
  );
  if (!org.data) return <p className="text-sm text-muted-foreground">Loading…</p>;
  const value = name ?? org.data.name;

  return (
    <form
      className="flex max-w-md flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        rename.mutate(value, { onSuccess: () => setName(null) });
      }}
    >
      <div className="flex flex-col gap-2">
        <Label htmlFor="org-name">Organization name</Label>
        <Input id="org-name" value={value} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="flex flex-col gap-1 text-sm">
        <span className="font-medium">Organization ID</span>
        <span className="font-mono text-muted-foreground">{org.data.slug}</span>
        <span className="text-xs text-muted-foreground">
          Share it with people who should request access at /signup.
        </span>
      </div>
      <Button
        type="submit"
        className="w-fit"
        disabled={rename.isPending || value.trim().length < 2 || value === org.data.name}
      >
        Save
      </Button>
    </form>
  );
}
