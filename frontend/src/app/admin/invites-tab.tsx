"use client";

import { useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import { useAdminMutation, useInvites } from "@/lib/admin-api";

export function InvitesTab() {
  const invites = useInvites();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"MEMBER" | "OWNER">("MEMBER");
  const [lastLink, setLastLink] = useState<{ link: string; emailSent: boolean } | null>(null);
  const create = useAdminMutation(
    async () => unwrap(await api.POST("/api/admin/invites", { body: { email, role } })),
    (r) => `Invitation created for ${r.invite.email}`,
  );
  const revoke = useAdminMutation(
    async (invite_id: string) =>
      unwrap(await api.DELETE("/api/admin/invites/{invite_id}", { params: { path: { invite_id } } })),
    "Invitation revoked",
  );

  return (
    <div className="flex flex-col gap-4">
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate(undefined, {
            onSuccess: (r) => {
              setLastLink({ link: r.link, emailSent: r.email_sent });
              setEmail("");
            },
          });
        }}
      >
        <Input
          aria-label="Email to invite"
          type="email"
          placeholder="name@company.com"
          className="max-w-xs"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <NativeSelect aria-label="Role" value={role} onChange={(e) => setRole(e.target.value as "MEMBER" | "OWNER")}>
          <option value="MEMBER">Member</option>
          <option value="OWNER">Owner</option>
        </NativeSelect>
        <Button type="submit" disabled={!email || create.isPending}>
          Send invitation
        </Button>
      </form>
      {lastLink && (
        <Alert>
          <AlertTitle>
            {lastLink.emailSent ? "Invitation emailed" : "Email not sent: share this link yourself"}
          </AlertTitle>
          <AlertDescription>
            <code className="break-all">{lastLink.link}</code>
            <Button
              size="sm"
              variant="outline"
              onClick={() => navigator.clipboard.writeText(lastLink.link).then(() => toast.success("Link copied"))}
            >
              Copy link
            </Button>
          </AlertDescription>
        </Alert>
      )}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Email</TableHead>
            <TableHead>Role</TableHead>
            <TableHead>Expires</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {invites.data?.map((invite) => (
            <TableRow key={invite.id}>
              <TableCell>{invite.email}</TableCell>
              <TableCell>{invite.role.toLowerCase()}</TableCell>
              <TableCell className="text-xs text-muted-foreground">
                {new Date(invite.expires_at).toLocaleString()}
              </TableCell>
              <TableCell className="text-right">
                <Button size="sm" variant="ghost" className="text-destructive" onClick={() => revoke.mutate(invite.id)}>
                  Revoke
                </Button>
              </TableCell>
            </TableRow>
          ))}
          {invites.data?.length === 0 && (
            <TableRow>
              <TableCell colSpan={4} className="py-6 text-center text-muted-foreground">
                No pending invitations.
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  );
}
