"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { api, type Schemas } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import { useAdminMutation } from "@/lib/admin-api";
import { usePresets } from "@/lib/llm-api";

type Policy = Schemas["OrgPolicy"];
type Kind = Schemas["ProviderKind"];

const KINDS: { kind: Kind; label: string }[] = [
  { kind: "OPENAI_COMPATIBLE", label: "Cloud APIs (OpenAI-compatible)" },
  { kind: "SERVER_LOCAL", label: "Self-hosted servers on this network" },
  { kind: "BRIDGE", label: "Models on members' own PCs (Local Model Bridge)" },
  { kind: "GGUF_MANAGED", label: "Model files managed by this server" },
];

function PolicyForm({ initial }: { initial: Policy }) {
  const presets = usePresets();
  const [policy, setPolicy] = useState<Policy>(initial);
  const save = useAdminMutation(
    async () => unwrap(await api.PUT("/api/admin/org-policy", { body: policy })),
    "AI policy saved",
  );
  const kinds = policy.allowed_provider_kinds ?? [];
  const restrictPresets = policy.allowed_preset_keys != null;

  return (
    <div className="flex flex-col gap-4 text-sm">
      <div className="flex items-start justify-between gap-4">
        <Label htmlFor="pii" className="flex-col items-start gap-1">
          Keep candidate data away from providers that may train on it
          <span className="text-xs font-normal text-muted-foreground">
            Strongly recommended. CVs and transcripts only go to models whose provider does not train on API data.
          </span>
        </Label>
        <Switch
          id="pii"
          checked={policy.block_training_providers_for_pii ?? true}
          onCheckedChange={(c) => setPolicy({ ...policy, block_training_providers_for_pii: c })}
        />
      </div>
      <div className="flex items-center justify-between gap-4">
        <Label htmlFor="member-models">Members may add their own models and API keys</Label>
        <Switch
          id="member-models"
          checked={policy.allow_member_llm_profiles ?? true}
          onCheckedChange={(c) => setPolicy({ ...policy, allow_member_llm_profiles: c })}
        />
      </div>
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 font-medium">Allowed provider types</legend>
        {KINDS.map(({ kind, label }) => (
          <label key={kind} className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={kinds.includes(kind)}
              onChange={(e) =>
                setPolicy({
                  ...policy,
                  allowed_provider_kinds: e.target.checked ? [...kinds, kind] : kinds.filter((k) => k !== kind),
                })
              }
            />
            {label}
          </label>
        ))}
      </fieldset>
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 font-medium">Cloud providers</legend>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={restrictPresets}
            onChange={(e) => setPolicy({ ...policy, allowed_preset_keys: e.target.checked ? [] : null })}
          />
          Only allow the providers selected below
        </label>
        {restrictPresets && (
          <div className="grid gap-1 pl-6 sm:grid-cols-2">
            {presets.data
              ?.filter((p) => p.kind === "OPENAI_COMPATIBLE")
              .map((p) => {
                const selected = policy.allowed_preset_keys ?? [];
                return (
                  <label key={p.key} className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={selected.includes(p.key)}
                      onChange={(e) =>
                        setPolicy({
                          ...policy,
                          allowed_preset_keys: e.target.checked
                            ? [...selected, p.key]
                            : selected.filter((k) => k !== p.key),
                        })
                      }
                    />
                    {p.display_name}
                  </label>
                );
              })}
          </div>
        )}
      </fieldset>
      <Button className="w-fit" disabled={save.isPending} onClick={() => save.mutate(undefined)}>
        Save AI policy
      </Button>
    </div>
  );
}

export function AiPolicyCard() {
  const policy = useQuery({
    queryKey: ["admin", "org-policy"],
    queryFn: async () => unwrap(await api.GET("/api/admin/org-policy")),
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>AI policy</CardTitle>
        <CardDescription>Applies to every model call in your organization. Changes are audited.</CardDescription>
      </CardHeader>
      <CardContent>
        {policy.data ? (
          <PolicyForm key={policy.dataUpdatedAt} initial={policy.data} />
        ) : (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
      </CardContent>
    </Card>
  );
}
