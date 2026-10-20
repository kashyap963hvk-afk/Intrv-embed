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
import { Switch } from "@/components/ui/switch";
import { useSpaces } from "@/lib/knowledge-api";
import {
  buildCreateSetIn,
  CATEGORIES,
  categoryLabel,
  difficultyLabel,
  documentOptions,
  EMPTY_SET_FORM,
  MAX_COUNT,
  MIN_COUNT,
  mixPercentages,
  SENIORITY_OPTIONS,
  setFormReady,
  toggleId,
  type DocumentOption,
  type SetForm,
} from "@/lib/questions";
import { useCreateSet, useSpaceDocuments } from "@/lib/questions-api";
import { SENIORITY } from "@/lib/rolepacks";
import { useRolePacks } from "@/lib/rolepacks-api";

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

function toSeniority(value: string): SetForm["seniority"] {
  return SENIORITY.find((s) => s === value) ?? "";
}

/** The form; mounted only while the dialog is open, so documents are fetched (and state reset) per opening. */
function NewSetForm({ onCreated }: { onCreated: () => void }) {
  const [form, setForm] = useState<SetForm>(EMPTY_SET_FORM);
  const spaces = useSpaces();
  const spaceList = spaces.data ?? [];
  const documents = useSpaceDocuments(spaceList);
  const packs = useRolePacks();
  const create = useCreateSet();
  const router = useRouter();

  const docLists = documents.map((q) => q.data);
  const cvs = documentOptions(spaceList, docLists, "CV");
  const jds = documentOptions(spaceList, docLists, "JD");
  const loadingDocs = spaces.isPending || documents.some((q) => q.isPending);
  const pack = packs.data?.find((p) => p.id === form.rolePackId);
  const percentages = mixPercentages(form.mix);
  const update = (patch: Partial<SetForm>) => setForm((current) => ({ ...current, ...patch }));

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate(buildCreateSetIn(form), {
          onSuccess: (set) => {
            onCreated();
            router.push(`/app/questions/${set.id}`);
          },
        });
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-2">
          <Label htmlFor="qs-cv">CV</Label>
          <DocumentSelect
            id="qs-cv"
            value={form.cvDocumentId}
            options={cvs}
            empty={loadingDocs ? "Loading…" : cvs.length === 0 ? "No ready CVs" : "Choose a CV"}
            onChange={(cvDocumentId) => update({ cvDocumentId })}
          />
          {!loadingDocs && cvs.length === 0 && (
            <p className="text-xs text-muted-foreground">
              Upload a CV in{" "}
              <Link href="/app/knowledge" className="underline">
                Knowledge
              </Link>{" "}
              (type CV) and wait until it is ready.
            </p>
          )}
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="qs-jd">Job description (optional)</Label>
          <DocumentSelect
            id="qs-jd"
            value={form.jdDocumentId}
            options={jds}
            empty="No job description"
            onChange={(jdDocumentId) => update({ jdDocumentId })}
          />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="qs-pack">Role pack</Label>
          <NativeSelect
            id="qs-pack"
            value={form.rolePackId}
            onChange={(e) => update({ rolePackId: e.target.value, roleVariant: "" })}
          >
            <option value="">{packs.isPending ? "Loading…" : "Choose a role pack"}</option>
            {packs.data?.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="qs-variant">Role variant</Label>
          <NativeSelect
            id="qs-variant"
            value={form.roleVariant}
            disabled={!pack}
            onChange={(e) => update({ roleVariant: e.target.value })}
          >
            <option value="">{pack?.variants[0] ? `Default (${pack.variants[0].name})` : "Pick a pack first"}</option>
            {pack?.variants.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="qs-count">Number of questions</Label>
          <Input
            id="qs-count"
            type="number"
            min={MIN_COUNT}
            max={MAX_COUNT}
            value={Number.isFinite(form.count) ? form.count : ""}
            onChange={(e) => update({ count: e.target.valueAsNumber })}
          />
          <p className="text-xs text-muted-foreground">
            {MIN_COUNT} to {MAX_COUNT}.
          </p>
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="qs-seniority">Seniority</Label>
          <NativeSelect
            id="qs-seniority"
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
          <Label htmlFor="qs-difficulty">Difficulty</Label>
          <NativeSelect
            id="qs-difficulty"
            value={form.difficulty}
            onChange={(e) => update({ difficulty: Number(e.target.value) })}
          >
            <option value={0}>Follow seniority (mixed)</option>
            {[1, 2, 3, 4, 5].map((d) => (
              <option key={d} value={d}>
                {d}: {difficultyLabel(d)}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="qs-title">Title (optional)</Label>
          <Input
            id="qs-title"
            value={form.title}
            maxLength={200}
            placeholder="CV title · role variant"
            onChange={(e) => update({ title: e.target.value })}
          />
        </div>
      </div>

      <div className="flex flex-col gap-3 rounded-lg border p-3">
        <label className="flex items-center justify-between gap-3 text-sm font-medium">
          Customise the category mix
          <Switch checked={form.customMix} onCheckedChange={(customMix) => update({ customMix })} />
        </label>
        {form.customMix && (
          <div className="flex flex-col gap-2">
            {CATEGORIES.map((category) => (
              <div key={category} className="grid items-center gap-2 sm:grid-cols-[8rem_1fr_3rem]">
                <Label htmlFor={`qs-mix-${category}`} className="font-normal">
                  {categoryLabel(category)}
                </Label>
                <input
                  id={`qs-mix-${category}`}
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={form.mix[category]}
                  className="w-full accent-primary"
                  onChange={(e) => update({ mix: { ...form.mix, [category]: Number(e.target.value) } })}
                />
                <span className="text-right text-sm tabular-nums">{percentages[category]}%</span>
              </div>
            ))}
            <p className="text-xs text-muted-foreground">
              Shares are relative; behavioral questions need behavioral competencies in the role pack.
            </p>
          </div>
        )}
      </div>

      {spaceList.length > 0 && (
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-sm font-medium">Ground model answers in (optional)</legend>
          <p className="text-xs text-muted-foreground">
            Knowledge spaces to cite in rationales and model answers. None selected: every space you can read.
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

      <DialogFooter>
        <Button type="submit" disabled={!setFormReady(form) || create.isPending}>
          {create.isPending ? "Starting…" : "Generate questions"}
        </Button>
      </DialogFooter>
    </form>
  );
}

/** Generate a question set from a CV (+ optional JD) and a role pack; generation continues in the background. */
export function NewSetDialog() {
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Plus /> New question set
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>New question set</DialogTitle>
          <DialogDescription>
            Questions grounded in the candidate&apos;s CV, weighted by the role pack and aimed at gaps against the job
            description. Generation runs in the background and takes a minute or two.
          </DialogDescription>
        </DialogHeader>
        <NewSetForm onCreated={() => setOpen(false)} />
      </DialogContent>
    </Dialog>
  );
}
