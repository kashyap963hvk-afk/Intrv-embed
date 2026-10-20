"use client";

import { ArrowLeft, Trash, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { RequireAuth } from "@/components/auth/require-auth";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { isOwner, type Me } from "@/lib/auth";
import {
  buildTemplatePatch,
  insertVariable,
  PERSONA_VARIABLES,
  sameTemplateForm,
  TEMPLATE_LIMITS,
  templateForm,
  templateFormProblem,
  type Template,
  type TemplateForm,
} from "@/lib/interviews";
import { useDeleteTemplate, useTemplate, useUpdateTemplate } from "@/lib/interviews-api";
import { useRolePacks } from "@/lib/rolepacks-api";

import { SettingsFields } from "../../settings-fields";
import { PromptPreviewPanel } from "./prompt-preview";
import { SimulatePanel } from "./simulate-panel";

function PersonaField({
  value,
  disabled,
  onChange,
}: {
  value: string;
  disabled: boolean;
  onChange: (next: string) => void;
}) {
  const field = useRef<HTMLTextAreaElement>(null);

  function insert(variable: string) {
    const el = field.current;
    const { text, cursor } = insertVariable(
      value,
      el?.selectionStart ?? value.length,
      el?.selectionEnd ?? value.length,
      variable,
    );
    onChange(text);
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(cursor, cursor);
    });
  }

  return (
    <div className="flex flex-col gap-2">
      <Label htmlFor="tpl-persona">Persona (prompt layer L2)</Label>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-xs text-muted-foreground">Insert:</span>
        {PERSONA_VARIABLES.map((variable) => (
          <Button
            key={variable}
            type="button"
            size="sm"
            variant="outline"
            className="h-7 font-mono text-xs"
            disabled={disabled}
            onClick={() => insert(variable)}
          >
            {`{{${variable}}}`}
          </Button>
        ))}
      </div>
      <Textarea
        id="tpl-persona"
        ref={field}
        rows={8}
        value={value}
        maxLength={TEMPLATE_LIMITS.personaMax}
        disabled={disabled}
        placeholder="Tone and style, e.g. Warm and direct. Starts broad, then digs into concrete details of the candidate's own work."
        onChange={(e) => onChange(e.target.value)}
      />
      <p className="text-xs text-muted-foreground">
        How the interviewer sounds. Empty uses the role pack&apos;s default style. The platform rules (one question per
        turn, no scores, no protected topics) always win.
      </p>
    </div>
  );
}

function Editor({ template, me }: { template: Template; me: Me }) {
  const [form, setForm] = useState<TemplateForm>(() => templateForm(template));
  const [saved, setSaved] = useState(() => templateForm(template));
  const packs = useRolePacks();
  const save = useUpdateTemplate(template.id);
  const remove = useDeleteTemplate();
  const router = useRouter();
  const canEdit = !template.shared || isOwner(me);
  const pack = packs.data?.find((p) => p.id === form.rolePackId);
  const dirty = !sameTemplateForm(form, saved);
  const problem = templateFormProblem(form);
  const update = (patch: Partial<TemplateForm>) => setForm((current) => ({ ...current, ...patch }));

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-1">
          <Link
            href="/app/interviews/templates"
            className="flex w-fit items-center gap-1 text-sm text-muted-foreground"
          >
            <ArrowLeft className="size-4" aria-hidden /> Templates
          </Link>
          <h1 className="flex flex-wrap items-center gap-2 text-2xl font-semibold tracking-tight">
            {template.name}
            {template.shared ? (
              <Badge variant="secondary">Organization</Badge>
            ) : (
              <Badge variant="outline">Only you</Badge>
            )}
          </h1>
          {!canEdit && (
            <p className="text-sm text-muted-foreground">Organization templates can only be changed by owners.</p>
          )}
        </div>
        {canEdit && (
          <Button
            size="sm"
            variant="outline"
            className="text-destructive"
            disabled={remove.isPending}
            onClick={() =>
              window.confirm(`Delete the template ${template.name}?`) &&
              remove.mutate(template, { onSuccess: () => router.push("/app/interviews/templates") })
            }
          >
            <Trash /> Delete
          </Button>
        )}
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <form
          className="flex flex-col gap-6"
          onSubmit={(e) => {
            e.preventDefault();
            if (problem) return;
            save.mutate(buildTemplatePatch(form, pack?.variants[0]?.id ?? null), {
              onSuccess: (updated) => {
                const next = templateForm(updated);
                setForm(next);
                setSaved(next);
              },
            });
          }}
        >
          <fieldset disabled={!canEdit} className="flex flex-col gap-6">
            <Card>
              <CardContent className="flex flex-col gap-4">
                <div className="flex flex-col gap-2">
                  <Label htmlFor="tpl-name">Name</Label>
                  <Input
                    id="tpl-name"
                    value={form.name}
                    maxLength={TEMPLATE_LIMITS.nameMax}
                    onChange={(e) => update({ name: e.target.value })}
                  />
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="flex flex-col gap-2">
                    <Label htmlFor="tpl-pack">Role pack</Label>
                    <NativeSelect
                      id="tpl-pack"
                      value={form.rolePackId}
                      onChange={(e) => update({ rolePackId: e.target.value, roleVariant: "" })}
                    >
                      {!form.rolePackId && (
                        <option value="">{packs.isPending ? "Loading…" : "Choose a role pack"}</option>
                      )}
                      {packs.data?.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                    </NativeSelect>
                  </div>
                  <div className="flex flex-col gap-2">
                    <Label htmlFor="tpl-variant">Role variant</Label>
                    <NativeSelect
                      id="tpl-variant"
                      value={form.roleVariant}
                      disabled={!pack}
                      onChange={(e) => update({ roleVariant: e.target.value })}
                    >
                      <option value="">
                        {pack?.variants[0] ? `Default (${pack.variants[0].name})` : "Pick a pack first"}
                      </option>
                      {pack?.variants.map((v) => (
                        <option key={v.id} value={v.id}>
                          {v.name}
                        </option>
                      ))}
                    </NativeSelect>
                  </div>
                </div>
                <div className="flex flex-col gap-2">
                  <Label htmlFor="tpl-interviewer">Interviewer name</Label>
                  <Input
                    id="tpl-interviewer"
                    value={form.interviewerName}
                    maxLength={TEMPLATE_LIMITS.interviewerNameMax}
                    placeholder="The role pack's interviewer name"
                    onChange={(e) => update({ interviewerName: e.target.value })}
                  />
                  <p className="text-xs text-muted-foreground">
                    Candidates are always told they are talking to an AI interviewer.
                  </p>
                </div>
                <PersonaField value={form.persona} disabled={!canEdit} onChange={(persona) => update({ persona })} />
                {template.warnings.length > 0 && (
                  <Alert>
                    <TriangleAlert aria-hidden />
                    <AlertTitle>Persona warnings (saved version)</AlertTitle>
                    <AlertDescription>
                      <ul className="list-disc space-y-0.5 pl-5">
                        {template.warnings.map((w) => (
                          <li key={w}>{w}</li>
                        ))}
                      </ul>
                      <p>The platform rules still apply at runtime; reword the persona to avoid surprises.</p>
                    </AlertDescription>
                  </Alert>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardContent className="flex flex-col gap-4">
                <h2 className="font-semibold">Default settings</h2>
                <SettingsFields
                  idPrefix="tpl-set"
                  value={form.settings}
                  onChange={(settings) => update({ settings })}
                />
              </CardContent>
            </Card>
          </fieldset>
          {canEdit && (
            <div className="flex flex-wrap items-center gap-3">
              <Button type="submit" disabled={!dirty || Boolean(problem) || save.isPending}>
                {save.isPending ? "Saving…" : "Save template"}
              </Button>
              {dirty && (
                <Button type="button" variant="ghost" onClick={() => setForm(saved)}>
                  Discard changes
                </Button>
              )}
              {problem && dirty && <p className="text-sm text-destructive">{problem}</p>}
            </div>
          )}
        </form>
        <div className="flex flex-col gap-6">
          <PromptPreviewPanel templateId={template.id} dirty={dirty} />
          <SimulatePanel templateId={template.id} hasPack={Boolean(template.role_pack_id)} dirty={dirty} />
        </div>
      </div>
    </div>
  );
}

function Workspace({ me }: { me: Me }) {
  const { id } = useParams<{ id: string }>();
  const template = useTemplate(id);

  if (template.isPending) return <p className="text-sm text-muted-foreground">Loading template…</p>;
  if (template.isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Template unavailable</AlertTitle>
        <AlertDescription>
          <p>{template.error.message}</p>
          <Link href="/app/interviews/templates" className={buttonVariants({ variant: "outline", size: "sm" })}>
            Back to templates
          </Link>
        </AlertDescription>
      </Alert>
    );
  }
  return <Editor key={template.data.id} template={template.data} me={me} />;
}

export function TemplateEditor() {
  return <RequireAuth>{(me) => <Workspace me={me} />}</RequireAuth>;
}
