"use client";

import { useId, useState, type ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Switch } from "@/components/ui/switch";
import {
  azureFormReady,
  buildAzureAccountIn,
  buildS3AccountIn,
  EMPTY_AZURE_FORM,
  EMPTY_S3_FORM,
  s3FormReady,
  type AzureForm,
  type ConnectorAccount,
  type CredentialAccountIn,
  type S3Form,
  type TestResult,
} from "@/lib/connectors";
import { useCreateAccount, useTestCredentials } from "@/lib/connectors-api";

function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function SwitchField({
  id,
  label,
  hint,
  checked,
  onChange,
}: {
  id: string;
  label: string;
  hint: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="flex flex-col gap-1">
        <Label htmlFor={id}>{label}</Label>
        <p className="text-xs text-muted-foreground">{hint}</p>
      </div>
      <Switch id={id} checked={checked} onCheckedChange={onChange} />
    </div>
  );
}

function S3Fields({
  form,
  onChange,
  owner,
}: {
  form: S3Form;
  onChange: (patch: Partial<S3Form>) => void;
  owner: boolean;
}) {
  const id = useId();
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Field id={`${id}-bucket`} label="Bucket">
        <Input
          id={`${id}-bucket`}
          value={form.bucket}
          maxLength={255}
          onChange={(e) => onChange({ bucket: e.target.value })}
          required
        />
      </Field>
      <Field id={`${id}-name`} label="Name (optional)">
        <Input
          id={`${id}-name`}
          value={form.name}
          maxLength={120}
          placeholder={form.bucket.trim() || "e.g. Hiring archive"}
          onChange={(e) => onChange({ name: e.target.value })}
        />
      </Field>
      <Field
        id={`${id}-endpoint`}
        label="Endpoint URL (optional)"
        hint="Empty for AWS S3. For R2, B2, Wasabi, MinIO and others, the provider's S3 endpoint."
      >
        <Input
          id={`${id}-endpoint`}
          type="url"
          value={form.endpointUrl}
          maxLength={500}
          placeholder="https://…"
          onChange={(e) => onChange({ endpointUrl: e.target.value })}
        />
      </Field>
      <Field id={`${id}-region`} label="Region">
        <Input
          id={`${id}-region`}
          value={form.region}
          maxLength={40}
          onChange={(e) => onChange({ region: e.target.value })}
          required
        />
      </Field>
      <Field id={`${id}-access`} label="Access key ID" hint="Leave both keys empty for a public bucket.">
        <Input
          id={`${id}-access`}
          value={form.accessKey}
          maxLength={256}
          autoComplete="off"
          spellCheck={false}
          onChange={(e) => onChange({ accessKey: e.target.value })}
        />
      </Field>
      <Field id={`${id}-secret`} label="Secret access key">
        <Input
          id={`${id}-secret`}
          type="password"
          value={form.secretKey}
          maxLength={512}
          autoComplete="new-password"
          onChange={(e) => onChange({ secretKey: e.target.value })}
        />
      </Field>
      <Field id={`${id}-prefix`} label="Folder prefix (optional)" hint="Only this prefix is ever listed or read.">
        <Input
          id={`${id}-prefix`}
          value={form.rootPrefix}
          maxLength={500}
          placeholder="e.g. hiring/2026"
          onChange={(e) => onChange({ rootPrefix: e.target.value })}
        />
      </Field>
      <div className="flex flex-col gap-3 sm:pt-6">
        <SwitchField
          id={`${id}-path-style`}
          label="Path-style addressing"
          hint="Needed by MinIO, Ceph and most non-AWS stores."
          checked={form.forcePathStyle}
          onChange={(forcePathStyle) => onChange({ forcePathStyle })}
        />
        {owner && (
          <SwitchField
            id={`${id}-org`}
            label="Organization storage"
            hint="Shared by owners; members never see it."
            checked={form.orgLevel}
            onChange={(orgLevel) => onChange({ orgLevel })}
          />
        )}
      </div>
    </div>
  );
}

function AzureFields({
  form,
  onChange,
  owner,
}: {
  form: AzureForm;
  onChange: (patch: Partial<AzureForm>) => void;
  owner: boolean;
}) {
  const id = useId();
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Field id={`${id}-mode`} label="Sign in with">
        <NativeSelect
          id={`${id}-mode`}
          value={form.mode}
          onChange={(e) => onChange({ mode: e.target.value === "sas_url" ? "sas_url" : "connection_string" })}
        >
          <option value="connection_string">Connection string</option>
          <option value="sas_url">Container SAS URL</option>
        </NativeSelect>
      </Field>
      <Field id={`${id}-name`} label="Name (optional)">
        <Input
          id={`${id}-name`}
          value={form.name}
          maxLength={120}
          placeholder={form.container.trim() || "e.g. Hiring archive"}
          onChange={(e) => onChange({ name: e.target.value })}
        />
      </Field>
      {form.mode === "connection_string" ? (
        <>
          <Field id={`${id}-conn`} label="Connection string" hint="Storage account → Access keys.">
            <Input
              id={`${id}-conn`}
              type="password"
              value={form.connectionString}
              maxLength={2000}
              autoComplete="new-password"
              onChange={(e) => onChange({ connectionString: e.target.value })}
              required
            />
          </Field>
          <Field id={`${id}-container`} label="Container">
            <Input
              id={`${id}-container`}
              value={form.container}
              maxLength={63}
              onChange={(e) => onChange({ container: e.target.value })}
              required
            />
          </Field>
        </>
      ) : (
        <Field id={`${id}-sas`} label="Container SAS URL" hint="Needs read and list permissions on the container.">
          <Input
            id={`${id}-sas`}
            type="password"
            value={form.sasUrl}
            maxLength={2000}
            autoComplete="new-password"
            placeholder="https://account.blob.core.windows.net/container?sv=…"
            onChange={(e) => onChange({ sasUrl: e.target.value })}
            required
          />
        </Field>
      )}
      <Field id={`${id}-prefix`} label="Folder prefix (optional)" hint="Only this prefix is ever listed or read.">
        <Input
          id={`${id}-prefix`}
          value={form.rootPrefix}
          maxLength={500}
          onChange={(e) => onChange({ rootPrefix: e.target.value })}
        />
      </Field>
      {owner && (
        <div className="sm:pt-6">
          <SwitchField
            id={`${id}-org`}
            label="Organization storage"
            hint="Shared by owners; members never see it."
            checked={form.orgLevel}
            onChange={(orgLevel) => onChange({ orgLevel })}
          />
        </div>
      )}
    </div>
  );
}

function TestOutcome({ result }: { result: TestResult }) {
  return (
    <p className="flex flex-wrap items-center gap-2 text-sm" aria-live="polite">
      <Badge variant={result.ok ? "success" : "destructive"}>{result.ok ? "works" : "failed"}</Badge>
      <span className={result.ok ? "text-muted-foreground" : "text-destructive"}>{result.detail}</span>
    </p>
  );
}

/** Credentials for a bucket or container, with Test (without saving) and Save (the server checks them again). */
export function CredentialsForm({
  kind,
  owner,
  onSaved,
  onCancel,
}: {
  kind: "S3_COMPATIBLE" | "AZURE_BLOB";
  owner: boolean;
  onSaved: (account: ConnectorAccount) => void;
  onCancel?: () => void;
}) {
  const [s3, setS3] = useState<S3Form>(EMPTY_S3_FORM);
  const [azure, setAzure] = useState<AzureForm>(EMPTY_AZURE_FORM);
  const test = useTestCredentials();
  const save = useCreateAccount();
  const ready = kind === "S3_COMPATIBLE" ? s3FormReady(s3) : azureFormReady(azure);

  function body(): CredentialAccountIn {
    return kind === "S3_COMPATIBLE" ? buildS3AccountIn(s3) : buildAzureAccountIn(azure);
  }

  return (
    <form
      className="flex flex-col gap-4 rounded-md border p-4"
      autoComplete="off"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate(body(), { onSuccess: onSaved });
      }}
    >
      {kind === "S3_COMPATIBLE" ? (
        <S3Fields
          form={s3}
          owner={owner}
          onChange={(patch) => {
            test.reset();
            setS3((current) => ({ ...current, ...patch }));
          }}
        />
      ) : (
        <AzureFields
          form={azure}
          owner={owner}
          onChange={(patch) => {
            test.reset();
            setAzure((current) => ({ ...current, ...patch }));
          }}
        />
      )}
      <p className="text-xs text-muted-foreground">
        Keys are encrypted on the server and never shown again. Read-only keys are enough.
      </p>
      {test.data && <TestOutcome result={test.data} />}
      <div className="flex flex-wrap justify-end gap-2">
        {onCancel && (
          <Button type="button" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        )}
        <Button
          type="button"
          variant="outline"
          disabled={!ready || test.isPending || save.isPending}
          onClick={() => test.mutate(body())}
        >
          {test.isPending ? "Testing…" : "Test"}
        </Button>
        <Button type="submit" disabled={!ready || save.isPending}>
          {save.isPending ? "Checking and saving…" : "Save"}
        </Button>
      </div>
    </form>
  );
}
