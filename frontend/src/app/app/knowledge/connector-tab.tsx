"use client";

import { Check, File, Folder, FolderOpen, HardDrive, X, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useId, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import {
  accountDetail,
  accountsOfKind,
  buildSourceIn,
  DEFAULT_INTERVAL_MINUTES,
  folderPrefix,
  kindLabel,
  MAX_SOURCE_ITEMS,
  mergeItems,
  oauthProvider,
  pickedToItems,
  ROOT_CRUMB,
  suggestSourceName,
  toggleItem,
  toSelectedItem,
  type ConnectorAccount,
  type ConnectorKindInfo,
  type Crumb,
  type SelectedItem,
} from "@/lib/connectors";
import {
  useCheckAccount,
  useConnectorAccounts,
  useCreateSource,
  useDeleteAccount,
  useOAuthConnect,
  usePickerToken,
} from "@/lib/connectors-api";
import { env } from "@/lib/env";
import { openGooglePicker } from "@/lib/google-picker";
import type { DocTypeChoice, Space } from "@/lib/knowledge";

import { CredentialsForm } from "./credentials-form";
import { DocTypeSelect } from "./doc-type-select";
import { RemoteBrowser } from "./remote-browser";
import { SyncControls } from "./sync-controls";

function SelectedChip({ icon: Icon, label, onRemove }: { icon: LucideIcon; label: string; onRemove: () => void }) {
  return (
    <li className="inline-flex max-w-64 items-center gap-1 rounded-md border px-2 py-0.5 text-xs">
      <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
      <span className="truncate" title={label}>
        {label}
      </span>
      <button
        type="button"
        aria-label={`Remove ${label}`}
        className="rounded-xs text-muted-foreground hover:text-foreground"
        onClick={onRemove}
      >
        <X className="size-3.5" />
      </button>
    </li>
  );
}

interface PanelProps {
  info: ConnectorKindInfo;
  account: ConnectorAccount;
  space: Space;
  onDone: () => void;
  onPickerOpenChange: (open: boolean) => void;
}

/** Choose what to import from one account (browser, or the Google Picker for Drive), then name and schedule it. */
function SelectionPanel({ info, account, space, onDone, onPickerOpenChange }: PanelProps) {
  const kind = info.kind;
  const bucket = kind === "S3_COMPATIBLE" || kind === "AZURE_BLOB";
  const drive = kind === "GOOGLE_DRIVE";
  const rootName = (bucket && accountDetail(account)) || kindLabel(kind);

  const [items, setItems] = useState<SelectedItem[]>([]);
  const [prefix, setPrefix] = useState<string | null>(null);
  const [trail, setTrail] = useState<Crumb[]>([{ ...ROOT_CRUMB, name: rootName }]);
  const [browsing, setBrowsing] = useState(!drive);
  const [opening, setOpening] = useState(false);
  const [name, setName] = useState("");
  const [docType, setDocType] = useState<DocTypeChoice>("AUTO");
  const [syncEnabled, setSyncEnabled] = useState(false);
  const [intervalMinutes, setIntervalMinutes] = useState(DEFAULT_INTERVAL_MINUTES);
  const pickerButton = useRef<HTMLButtonElement>(null);
  const pickerToken = usePickerToken();
  const create = useCreateSource(space.id);
  const id = useId();

  const selectedIds = useMemo(() => new Set(items.map((i) => i.id)), [items]);
  const suggested = suggestSourceName(items, prefix, rootName);
  const currentPrefix = bucket ? folderPrefix(account, trail[trail.length - 1].id) : null;
  const nothing = items.length === 0 && prefix === null;
  const tooMany = items.length > MAX_SOURCE_ITEMS;
  const count = items.length + (prefix !== null ? 1 : 0);

  async function openPicker() {
    // Failures are reported by the mutation.
    const token = await pickerToken.mutateAsync(account.id).catch(() => null);
    if (!token) return;
    if (!token.developer_key) {
      setBrowsing(true);
      toast.info("The Google Picker is not set up on this server. Choose from the files the app can open instead.");
      return;
    }
    setOpening(true);
    try {
      await openGooglePicker({
        accessToken: token.access_token,
        developerKey: token.developer_key,
        appId: env.googleAppId,
        onPicked: (docs) => setItems((current) => mergeItems(current, pickedToItems(docs))),
        onClosed: () => {
          // Show the dialog again before returning focus to the button that opened the picker.
          flushSync(() => onPickerOpenChange(false));
          pickerButton.current?.focus();
        },
      });
      // Hide this dialog (and release its focus trap) while the picker is up.
      onPickerOpenChange(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not open the Google Picker.");
    } finally {
      setOpening(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {drive && (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <Button
              ref={pickerButton}
              type="button"
              disabled={pickerToken.isPending || opening}
              onClick={() => void openPicker()}
            >
              <HardDrive /> {pickerToken.isPending || opening ? "Opening Google Drive…" : "Choose from Google Drive"}
            </Button>
            <Button type="button" variant="link" size="sm" onClick={() => setBrowsing((b) => !b)}>
              {browsing ? "Hide file list" : "Browse files the app can open"}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            The app only gets access to the files and folders you choose, nothing else in your Drive.
          </p>
        </div>
      )}

      {browsing && (
        <RemoteBrowser
          accountId={account.id}
          trail={trail}
          onTrailChange={setTrail}
          selectedIds={selectedIds}
          onToggle={(item) => setItems((current) => toggleItem(current, toSelectedItem(item)))}
          emptyHint={
            drive && trail.length === 1
              ? "Nothing yet: files and folders you choose with Google Drive appear here."
              : "This folder is empty."
          }
          actions={
            currentPrefix !== null &&
            (prefix === currentPrefix ? (
              <Badge variant="secondary">
                <Check /> Whole folder selected
              </Badge>
            ) : (
              <Button type="button" size="sm" variant="outline" onClick={() => setPrefix(currentPrefix)}>
                <FolderOpen /> Import the whole folder
              </Button>
            ))
          }
        />
      )}

      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-2">
          <p className="text-sm font-medium">Selected{count > 0 && ` (${count})`}</p>
          {!nothing && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => {
                setItems([]);
                setPrefix(null);
              }}
            >
              Clear
            </Button>
          )}
        </div>
        {nothing ? (
          <p className="text-sm text-muted-foreground">
            {drive && !browsing ? "Nothing chosen yet." : "Tick files or folders to import."}
          </p>
        ) : (
          <ul className="flex max-h-28 flex-wrap gap-1 overflow-y-auto" aria-label="Selected to import">
            {prefix !== null && (
              <SelectedChip
                icon={FolderOpen}
                label={`Whole folder: ${prefix || rootName}`}
                onRemove={() => setPrefix(null)}
              />
            )}
            {items.map((item) => (
              <SelectedChip
                key={item.id}
                icon={item.is_folder ? Folder : File}
                label={item.name || item.path || item.id}
                onRemove={() => setItems((current) => current.filter((i) => i.id !== item.id))}
              />
            ))}
          </ul>
        )}
        {tooMany ? (
          <p className="text-xs text-destructive">
            Choose at most {MAX_SOURCE_ITEMS} items, or pick the folder that holds them instead.
          </p>
        ) : (
          <p className="text-xs text-muted-foreground">Folders are imported with everything inside them.</p>
        )}
      </div>

      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate(
            buildSourceIn({
              kind,
              accountId: account.id,
              name: name.trim() || suggested,
              items,
              prefix,
              docType,
              syncEnabled,
              intervalMinutes,
            }),
            { onSuccess: onDone },
          );
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-2">
            <Label htmlFor={`${id}-name`}>Name</Label>
            <Input
              id={`${id}-name`}
              value={name}
              maxLength={200}
              placeholder={suggested}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <DocTypeSelect id={`${id}-type`} value={docType} onChange={setDocType} />
        </div>
        <SyncControls
          idPrefix={id}
          enabled={syncEnabled}
          onEnabledChange={setSyncEnabled}
          intervalMinutes={intervalMinutes}
          onIntervalChange={setIntervalMinutes}
        />
        <DialogFooter>
          <Button type="submit" disabled={nothing || tooMany || create.isPending}>
            {create.isPending ? "Starting…" : syncEnabled ? "Import and keep in sync" : "Import"}
          </Button>
        </DialogFooter>
      </form>
    </div>
  );
}

/** One storage tab of the "+" dialog: pick or connect an account, then choose what to import. */
export function ConnectorTab({
  info,
  space,
  owner,
  onDone,
  onPickerOpenChange,
}: {
  info: ConnectorKindInfo;
  space: Space;
  owner: boolean;
  onDone: () => void;
  onPickerOpenChange: (open: boolean) => void;
}) {
  const accounts = useConnectorAccounts();
  const [chosenId, setChosenId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  // A newly connected OAuth account is the newest one, which is picked when nothing is chosen.
  const oauth = useOAuthConnect(() => setChosenId(null));
  const check = useCheckAccount();
  const remove = useDeleteAccount();
  const selectId = useId();
  const provider = oauthProvider(info.kind);
  const credentialsKind = info.kind === "S3_COMPATIBLE" || info.kind === "AZURE_BLOB" ? info.kind : null;

  if (!info.available) {
    return (
      <Alert>
        <AlertTitle>{info.label} is not set up on this server</AlertTitle>
        <AlertDescription>{info.reason ?? "Ask the administrator of this installation to enable it."}</AlertDescription>
      </Alert>
    );
  }
  if (accounts.isPending) return <p className="text-sm text-muted-foreground">Loading accounts…</p>;
  if (accounts.isError) return <p className="text-sm text-destructive">{accounts.error.message}</p>;

  const mine = accountsOfKind(accounts.data, info.kind);
  const account = mine.find((a) => a.id === chosenId) ?? mine.at(-1) ?? null;
  const connectButton = provider && (
    <Button
      type="button"
      variant={account ? "outline" : "default"}
      size={account ? "sm" : "default"}
      disabled={oauth.pending !== null}
      onClick={() => oauth.connect(provider)}
    >
      {oauth.pending ? "Waiting for sign-in…" : account ? "Connect another" : `Connect ${info.label}`}
    </Button>
  );

  if (credentialsKind && (adding || !account)) {
    return (
      <div className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">
          {credentialsKind === "S3_COMPATIBLE"
            ? "Connect any S3-compatible bucket: AWS S3, Cloudflare R2, Backblaze B2, Wasabi, MinIO and more."
            : "Connect an Azure Blob Storage container with a connection string or a SAS URL."}
        </p>
        <CredentialsForm
          kind={credentialsKind}
          owner={owner}
          onSaved={(saved) => {
            setChosenId(saved.id);
            setAdding(false);
          }}
          onCancel={account ? () => setAdding(false) : undefined}
        />
      </div>
    );
  }

  if (!account) {
    if (info.kind === "GOOGLE_DRIVE_ORG") {
      return (
        <Alert>
          <AlertTitle>No shared folders yet</AlertTitle>
          <AlertDescription>
            <p>
              Share Drive folders with the service account and register them under{" "}
              <Link href="/admin" className="underline underline-offset-4">
                Administration → Spaces
              </Link>
              .
            </p>
          </AlertDescription>
        </Alert>
      );
    }
    return (
      <div className="flex flex-col items-start gap-3">
        <p className="text-sm text-muted-foreground">
          Sign in to {info.label} in a pop-up window. Access is read-only and you can disconnect at any time.
        </p>
        {connectButton}
      </div>
    );
  }

  const detail = accountDetail(account);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-end gap-2">
          <div className="flex min-w-48 flex-1 flex-col gap-2">
            <Label htmlFor={selectId}>Account</Label>
            <NativeSelect id={selectId} value={account.id} onChange={(e) => setChosenId(e.target.value)}>
              {mine.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                  {a.org_level ? " · organization" : ""}
                </option>
              ))}
            </NativeSelect>
          </div>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={check.isPending}
            onClick={() => check.mutate(account)}
          >
            {check.isPending ? "Testing…" : "Test"}
          </Button>
          {(!account.org_level || owner) && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="text-destructive"
              disabled={remove.isPending}
              onClick={() =>
                window.confirm(
                  `Disconnect ${account.name}? Sources that use it are removed and stop syncing; their documents stay.`,
                ) && remove.mutate(account, { onSuccess: () => setChosenId(null) })
              }
            >
              Disconnect
            </Button>
          )}
          {connectButton}
          {credentialsKind && (
            <Button type="button" size="sm" variant="outline" onClick={() => setAdding(true)}>
              Add another
            </Button>
          )}
        </div>
        {detail && !account.name.includes(detail) && <p className="text-xs text-muted-foreground">{detail}</p>}
        {account.status === "ERROR" && (
          <p className="text-xs text-destructive">{account.last_error ?? "The last check of this account failed."}</p>
        )}
      </div>
      <SelectionPanel
        key={account.id}
        info={info}
        account={account}
        space={space}
        onDone={onDone}
        onPickerOpenChange={onPickerOpenChange}
      />
    </div>
  );
}
