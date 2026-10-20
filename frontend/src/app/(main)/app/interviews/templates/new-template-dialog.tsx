"use client";

import { Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
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
import { buildTemplateIn, TEMPLATE_LIMITS } from "@/lib/interviews";
import { useCreateTemplate } from "@/lib/interviews-api";
import { useRolePacks } from "@/lib/rolepacks-api";

function NewTemplateForm({ owner, onCreated }: { owner: boolean; onCreated: () => void }) {
  const [form, setForm] = useState({ name: "", rolePackId: "", roleVariant: "", shared: false });
  const packs = useRolePacks();
  const create = useCreateTemplate();
  const router = useRouter();
  const pack = packs.data?.find((p) => p.id === form.rolePackId);
  const update = (patch: Partial<typeof form>) => setForm((current) => ({ ...current, ...patch }));

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate(buildTemplateIn(form), {
          onSuccess: (template) => {
            onCreated();
            router.push(`/app/interviews/templates/${template.id}`);
          },
        });
      }}
    >
      <div className="flex flex-col gap-2">
        <Label htmlFor="tpl-name">Name</Label>
        <Input
          id="tpl-name"
          value={form.name}
          maxLength={TEMPLATE_LIMITS.nameMax}
          placeholder="e.g. Firmware engineer, friendly and thorough"
          autoFocus
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
            <option value="">{packs.isPending ? "Loading…" : "Choose later"}</option>
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
            <option value="">{pack?.variants[0] ? `Default (${pack.variants[0].name})` : "Pick a pack first"}</option>
            {pack?.variants.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
              </option>
            ))}
          </NativeSelect>
        </div>
      </div>
      {owner && (
        <label className="flex items-center justify-between gap-3 rounded-lg border p-3 text-sm font-medium">
          <span className="flex flex-col">
            Share with the organization
            <span className="text-xs font-normal text-muted-foreground">
              Everyone can use it; only owners can change it. This cannot be changed later.
            </span>
          </span>
          <Switch checked={form.shared} onCheckedChange={(shared) => update({ shared })} />
        </label>
      )}
      <DialogFooter>
        <Button type="submit" disabled={!form.name.trim() || create.isPending}>
          {create.isPending ? "Creating…" : "Create template"}
        </Button>
      </DialogFooter>
    </form>
  );
}

export function NewTemplateDialog({ owner }: { owner: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Plus /> New template
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New interview template</DialogTitle>
          <DialogDescription>
            A reusable interviewer persona and default settings. You can edit the persona and settings next.
          </DialogDescription>
        </DialogHeader>
        <NewTemplateForm owner={owner} onCreated={() => setOpen(false)} />
      </DialogContent>
    </Dialog>
  );
}
