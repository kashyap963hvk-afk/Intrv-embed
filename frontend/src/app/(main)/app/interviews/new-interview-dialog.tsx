"use client";

import { Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import {
  buildCreateInterviewIn,
  DEFAULT_SETTINGS,
  EMPTY_INTERVIEW_FORM,
  interviewFormReady,
  parseSettings,
  type InterviewForm,
} from "@/lib/interviews";
import { useCreateInterview, useTemplates } from "@/lib/interviews-api";
import { useSpaces } from "@/lib/knowledge-api";
import { documentOptions, SENIORITY_OPTIONS, toggleId, type DocumentOption } from "@/lib/questions";
import { useQuestionSets, useSpaceDocuments } from "@/lib/questions-api";
import { SENIORITY } from "@/lib/rolepacks";
import { useRolePacks } from "@/lib/rolepacks-api";

import { OverrideFields } from "./settings-fields";

function DocumentSelect({
  id,
  value,
  options,
  empty,
  onChange,
}: {
  id: string;
  value: string;
  options: DocumentOption[];
  empty: string;
  onChange: (id: string) => void;
}) {
  return (
    <NativeSelect id={id} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{empty}</option>
      {options.map((doc) => (
        <option key={doc.id} value={doc.id}>
          {doc.title} ({doc.spaceName})
        </option>
      ))}
    </NativeSelect>
  );
}

function toSeniority(value: string): InterviewForm["seniority"] {
  return SENIORITY.find((s) => s === value) ?? "";
}

/** The form; mounted only while the dialog is open, so lists are fetched (and state reset) per opening. */
function NewInterviewForm({ onCreated }: { onCreated: () => void }) {
  const [form, setForm] = useState<InterviewForm>(EMPTY_INTERVIEW_FORM);
  const templates = useTemplates();
  const packs = useRolePacks();
  const spaces = useSpaces();
  const spaceList = spaces.data ?? [];
  const documents = useSpaceDocuments(spaceList);
  const sets = useQuestionSets();
  const create = useCreateInterview();
  const router = useRouter();

  const docLists = documents.map((q) => q.data);
  const cvs = documentOptions(spaceList, docLists, "CV");
  const jds = documentOptions(spaceList, docLists, "JD");
  const loadingDocs = spaces.isPending || documents.some((q) => q.isPending);
  const readySets = (sets.data ?? []).filter((s) => s.status === "READY");
  const template = templates.data?.find((t) => t.id === form.templateId);
  const templatePack = packs.data?.find((p) => p.id === template?.role_pack_id);
  const pack = packs.data?.find((p) => p.id === (form.rolePackId || template?.role_pack_id));
  const defaults = template ? parseSettings(template.settings) : DEFAULT_SETTINGS;
  const update = (patch: Partial<InterviewForm>) => setForm((current) => ({ ...current, ...patch }));
  const variantDefault = form.rolePackId
    ? pack?.variants[0]?.name
    : (pack?.variants.find((v) => v.id === template?.role_variant)?.name ?? pack?.variants[0]?.name);

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate(buildCreateInterviewIn(form), {
          onSuccess: (interview) => {
            onCreated();
            router.push(`/app/interviews/${interview.id}`);
          },
        });
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-2">
          <Label htmlFor="iv-candidate">Candidate</Label>
          <Input
            id="iv-candidate"
            value={form.candidateLabel}
            maxLength={120}
            placeholder="How the interviewer greets them, e.g. Sam Lee"
            onChange={(e) => update({ candidateLabel: e.target.value })}
          />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="iv-title">Title (optional)</Label>
          <Input
            id="iv-title"
            value={form.title}
            maxLength={200}
            placeholder="Candidate · role variant"
            onChange={(e) => update({ title: e.target.value })}
          />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="iv-template">Template (optional)</Label>
          <NativeSelect
            id="iv-template"
            value={form.templateId}
            onChange={(e) => update({ templateId: e.target.value, roleVariant: "", overrides: {} })}
          >
            <option value="">{templates.isPending ? "Loading…" : "No template"}</option>
            {templates.data?.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
                {t.shared ? " (organization)" : ""}
              </option>
            ))}
          </NativeSelect>
          <p className="text-xs text-muted-foreground">
            Persona and default settings.{" "}
            <Link href="/app/interviews/templates" className="underline">
              Manage templates
            </Link>
          </p>
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="iv-pack">Role pack</Label>
          <NativeSelect
            id="iv-pack"
            value={form.rolePackId}
            onChange={(e) => update({ rolePackId: e.target.value, roleVariant: "" })}
          >
            <option value="">
              {packs.isPending
                ? "Loading…"
                : templatePack
                  ? `From the template (${templatePack.name})`
                  : "Choose a role pack"}
            </option>
            {packs.data?.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="iv-variant">Role variant</Label>
          <NativeSelect
            id="iv-variant"
            value={form.roleVariant}
            disabled={!pack}
            onChange={(e) => update({ roleVariant: e.target.value })}
          >
            <option value="">{variantDefault ? `Default (${variantDefault})` : "Pick a pack first"}</option>
            {pack?.variants.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="iv-seniority">Seniority</Label>
          <NativeSelect
            id="iv-seniority"
            value={form.seniority}
            onChange={(e) => update({ seniority: toSeniority(e.target.value) })}
          >
            <option value="">Estimate from the CV</option>
            {SENIORITY_OPTIONS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="iv-cv">CV (optional)</Label>
          <DocumentSelect
            id="iv-cv"
            value={form.cvDocumentId}
            options={cvs}
            empty={loadingDocs ? "Loading…" : cvs.length === 0 ? "No ready CVs" : "No CV"}
            onChange={(cvDocumentId) => update({ cvDocumentId })}
          />
          <p className="text-xs text-muted-foreground">
            The interviewer verifies the CV&apos;s claims. Upload CVs in{" "}
            <Link href="/app/knowledge" className="underline">
              Knowledge
            </Link>{" "}
            (type CV).
          </p>
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="iv-jd">Job description (optional)</Label>
          <DocumentSelect
            id="iv-jd"
            value={form.jdDocumentId}
            options={jds}
            empty="No job description"
            onChange={(jdDocumentId) => update({ jdDocumentId })}
          />
        </div>
        <div className="flex flex-col gap-2 sm:col-span-2">
          <Label htmlFor="iv-set">Question set (optional)</Label>
          <NativeSelect
            id="iv-set"
            value={form.questionSetId}
            onChange={(e) => update({ questionSetId: e.target.value })}
          >
            <option value="">{sets.isPending ? "Loading…" : "No question set"}</option>
            {readySets.map((s) => (
              <option key={s.id} value={s.id}>
                {s.title} ({s.item_count} questions)
              </option>
            ))}
          </NativeSelect>
          <p className="text-xs text-muted-foreground">Seed questions for the plan, from a generated set.</p>
        </div>
      </div>

      {spaceList.length > 0 && (
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-sm font-medium">Reference material (optional)</legend>
          <p className="text-xs text-muted-foreground">
            Knowledge spaces the interviewer may consult for facts. None selected: every space you can read.
          </p>
          <div className="grid max-h-36 gap-2 overflow-y-auto sm:grid-cols-2">
            {spaceList.map((space) => (
              <label key={space.id} className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={form.spaceIds.includes(space.id)}
                  onCheckedChange={() => update({ spaceIds: toggleId(form.spaceIds, space.id) })}
                />
                <span className="truncate">{space.name}</span>
              </label>
            ))}
          </div>
        </fieldset>
      )}

      <details className="rounded-lg border p-3">
        <summary className="cursor-pointer text-sm font-medium">
          Settings{template ? ` (defaults from ${template.name})` : ""}
        </summary>
        <div className="mt-3">
          <OverrideFields
            idPrefix="iv-set"
            value={form.overrides}
            defaults={defaults}
            integrityFromOrg={!template}
            onChange={(overrides) => update({ overrides })}
          />
        </div>
      </details>

      <DialogFooter>
        <Button type="submit" disabled={!interviewFormReady(form, template) || create.isPending}>
          {create.isPending ? "Creating…" : "Create and prepare"}
        </Button>
      </DialogFooter>
    </form>
  );
}

/** Create an interview; preparation (CV reading, planning, reference material) continues in the background. */
export function NewInterviewDialog() {
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Plus /> New interview
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>New interview</DialogTitle>
          <DialogDescription>
            A live text interview run by the AI interviewer. It is planned from the role pack, the CV and the job
            description; you can review and edit the plan before inviting the candidate.
          </DialogDescription>
        </DialogHeader>
        <NewInterviewForm onCreated={() => setOpen(false)} />
      </DialogContent>
    </Dialog>
  );
}
