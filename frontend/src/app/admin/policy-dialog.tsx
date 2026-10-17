"use client";

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
import { Switch } from "@/components/ui/switch";
import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import { useAdminMutation, type AdminUser } from "@/lib/admin-api";

const TOGGLES = [
  ["can_use_voice", "Voice interviews"],
  ["can_use_coding", "Coding and practical rounds"],
  ["can_edit_prompts", "Edit interviewer prompts and role packs"],
  ["can_create_llm_profiles", "Add their own AI models and API keys"],
] as const;

type ToggleKey = (typeof TOGGLES)[number][0];

export function PolicyDialog({ user }: { user: AdminUser }) {
  const [open, setOpen] = useState(false);
  const [flags, setFlags] = useState<Record<ToggleKey, boolean>>(() => ({
    can_use_voice: user.policy.can_use_voice,
    can_use_coding: user.policy.can_use_coding,
    can_edit_prompts: user.policy.can_edit_prompts,
    can_create_llm_profiles: user.policy.can_create_llm_profiles,
  }));
  const [quota, setQuota] = useState(user.policy.monthly_interview_quota?.toString() ?? "");
  const save = useAdminMutation(
    async () =>
      unwrap(
        await api.PATCH("/api/admin/users/{user_id}/policy", {
          params: { path: { user_id: user.id } },
          body: quota.trim()
            ? { ...flags, monthly_interview_quota: Number(quota) }
            : { ...flags, clear_interview_quota: true },
        }),
      ),
    `Permissions for ${user.name} saved`,
  );

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="ghost">
          Permissions
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Permissions for {user.name}</DialogTitle>
          <DialogDescription>Applies immediately to their next request.</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          {TOGGLES.map(([key, label]) => (
            <div key={key} className="flex items-center justify-between gap-4">
              <Label htmlFor={`${user.id}-${key}`}>{label}</Label>
              <Switch
                id={`${user.id}-${key}`}
                checked={flags[key]}
                onCheckedChange={(checked) => setFlags((f) => ({ ...f, [key]: checked }))}
              />
            </div>
          ))}
          <div className="flex flex-col gap-2">
            <Label htmlFor={`${user.id}-quota`}>Interviews per month</Label>
            <Input
              id={`${user.id}-quota`}
              inputMode="numeric"
              placeholder="Unlimited"
              value={quota}
              onChange={(e) => setQuota(e.target.value.replace(/\D/g, ""))}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button disabled={save.isPending} onClick={() => save.mutate(undefined, { onSuccess: () => setOpen(false) })}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
