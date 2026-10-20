"use client";

import { Copy } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { accountDetail, kindLabel, parseFolderIds, type ConnectorAccount } from "@/lib/connectors";
import {
  useCheckAccount,
  useConnectorAccounts,
  useConnectorKinds,
  useCreateGoogleOrgAccount,
  useDeleteAccount,
  useServiceAccount,
} from "@/lib/connectors-api";

const DEFAULT_NAME = "Organization Google Drive";

function folderCount(account: ConnectorAccount): number {
  const roots = account.config.root_folder_ids;
  return Array.isArray(roots) ? roots.length : 0;
}

function OrgAccountRow({ account }: { account: ConnectorAccount }) {
  const check = useCheckAccount();
  const remove = useDeleteAccount();
  const folders = folderCount(account);
  const detail = account.kind === "GOOGLE_DRIVE_ORG" ? `${folders} ${folders === 1 ? "folder" : "folders"}` : "";
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 py-2">
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="flex flex-wrap items-center gap-2 text-sm font-medium">
          {account.name}
          <Badge variant={account.status === "OK" ? "secondary" : "destructive"}>
            {account.status === "OK" ? "ok" : "error"}
          </Badge>
        </span>
        <span className="truncate text-xs text-muted-foreground">
          {[kindLabel(account.kind), detail || accountDetail(account)].filter(Boolean).join(" · ")}
        </span>
        {account.status === "ERROR" && account.last_error && (
          <span className="text-xs text-destructive">{account.last_error}</span>
        )}
      </div>
      <div className="flex gap-1">
        <Button size="sm" variant="outline" disabled={check.isPending} onClick={() => check.mutate(account)}>
          {check.isPending ? "Testing…" : "Test"}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="text-destructive"
          disabled={remove.isPending}
          onClick={() =>
            window.confirm(
              `Remove ${account.name}? Sources that use it are removed and stop syncing; their documents stay.`,
            ) && remove.mutate(account)
          }
        >
          Remove
        </Button>
      </div>
    </li>
  );
}

/** Owners: the service account to share Drive folders with, the folders registered for sync, and org storage. */
export function OrgDriveCard() {
  const serviceAccount = useServiceAccount();
  const kinds = useConnectorKinds();
  const accounts = useConnectorAccounts();
  const create = useCreateGoogleOrgAccount();
  const [name, setName] = useState(DEFAULT_NAME);
  const [folders, setFolders] = useState("");
  const parsed = parseFolderIds(folders);
  const reason = kinds.data?.find((k) => k.kind === "GOOGLE_DRIVE_ORG")?.reason;
  const orgAccounts = (accounts.data ?? []).filter((a) => a.org_level);
  const email = serviceAccount.data?.configured ? serviceAccount.data.email : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Organization Google Drive</CardTitle>
        <CardDescription>
          Sync shared Drive folders into organization spaces without anyone&apos;s personal sign-in. Share each folder
          with the service account below (Viewer is enough), then register it here. Owners then import it from Add → Org
          Drive in any space.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {serviceAccount.isError && <p className="text-sm text-destructive">{serviceAccount.error.message}</p>}
        {serviceAccount.data && !email && (
          <Alert>
            <AlertTitle>Not configured on this server</AlertTitle>
            <AlertDescription>
              {reason ?? "Set GOOGLE_SERVICE_ACCOUNT_JSON_BASE64 to a service account key and restart the backend."}
            </AlertDescription>
          </Alert>
        )}
        {email && (
          <>
            <div className="flex flex-col gap-1">
              <p className="text-sm font-medium">Share folders with</p>
              <div className="flex flex-wrap items-center gap-2">
                <code className="rounded bg-muted px-2 py-1 font-mono text-sm break-all">{email}</code>
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label="Copy the service account email"
                  onClick={() =>
                    void navigator.clipboard.writeText(email).then(
                      () => toast.success("Email copied"),
                      () => toast.error("Could not copy; select the email instead."),
                    )
                  }
                >
                  <Copy /> Copy
                </Button>
              </div>
            </div>
            <form
              className="flex flex-col gap-3"
              onSubmit={(e) => {
                e.preventDefault();
                if (!parsed.ok) return;
                create.mutate(
                  { name: name.trim() || DEFAULT_NAME, root_folder_ids: parsed.ids },
                  {
                    onSuccess: () => {
                      setFolders("");
                      setName(DEFAULT_NAME);
                    },
                  },
                );
              }}
            >
              <div className="flex flex-col gap-2">
                <Label htmlFor="org-drive-name">Name</Label>
                <Input
                  id="org-drive-name"
                  className="max-w-sm"
                  value={name}
                  maxLength={120}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="org-drive-folders">Shared folders</Label>
                <Textarea
                  id="org-drive-folders"
                  rows={4}
                  value={folders}
                  placeholder={"One folder id or link per line, e.g.\nhttps://drive.google.com/drive/folders/1AbC…"}
                  onChange={(e) => setFolders(e.target.value)}
                />
                {folders.trim() && !parsed.ok && <p className="text-xs text-destructive">{parsed.error}</p>}
                <p className="text-xs text-muted-foreground">
                  Each folder is checked before saving: it must already be shared with the service account.
                </p>
              </div>
              <div>
                <Button type="submit" disabled={!parsed.ok || create.isPending}>
                  {create.isPending ? "Checking folders…" : "Register folders"}
                </Button>
              </div>
            </form>
          </>
        )}
        <div className="flex flex-col gap-1">
          <p className="text-sm font-medium">Organization storage</p>
          {accounts.isError && <p className="text-sm text-destructive">{accounts.error.message}</p>}
          {accounts.data && orgAccounts.length === 0 && (
            <p className="text-sm text-muted-foreground">
              None yet. Registered Drive folders, and buckets added as organization storage, appear here.
            </p>
          )}
          {orgAccounts.length > 0 && (
            <ul className="divide-y">
              {orgAccounts.map((account) => (
                <OrgAccountRow key={account.id} account={account} />
              ))}
            </ul>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
