"use client";

import { useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
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
import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import { probeProfile, useLlmMutation, usePresets, type Preset } from "@/lib/llm-api";

const CUSTOM = "__custom__";

export function AddModelDialog({ canShare }: { canShare: boolean }) {
  const presets = usePresets();
  const [open, setOpen] = useState(false);
  const [presetKey, setPresetKey] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [scope, setScope] = useState<"personal" | "org">("personal");
  const [noTraining, setNoTraining] = useState(false);
  const preset: Preset | undefined = presets.data?.find((p) => p.key === presetKey);

  function choose(key: string) {
    const chosen = presets.data?.find((p) => p.key === key);
    setPresetKey(key);
    setBaseUrl(chosen?.base_url ?? "");
    setName(chosen?.display_name ?? "");
    setModel(chosen?.model_hints[0] ?? "");
    setModels([]);
  }

  function reset() {
    setPresetKey(null);
    setApiKey("");
    setModels([]);
    setNoTraining(false);
    setScope("personal");
  }

  const discover = useLlmMutation(async () =>
    unwrap(
      await api.POST("/api/llm/models/discover", {
        body: { preset_key: preset?.key ?? null, base_url: baseUrl || null, api_key: apiKey || null },
      }),
    ),
  );

  const save = useLlmMutation(
    async () => {
      const created = unwrap(
        await api.POST("/api/llm/profiles", {
          body: {
            name,
            scope,
            preset_key: preset?.key ?? null,
            base_url: baseUrl || null,
            api_key: apiKey || null,
            model,
            trains_on_data: canShare && noTraining ? false : null,
          },
        }),
      );
      toast.info(`${created.name} saved. Testing what it can do…`);
      return probeProfile(created.id);
    },
    (p) => (p.status === "OK" ? `${p.name} is ready` : `${p.name} saved, but the test failed: ${p.last_error ?? ""}`),
  );

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button>Add model</Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{presetKey ? "Connect the model" : "Choose a provider"}</DialogTitle>
          <DialogDescription>
            Any OpenAI-compatible endpoint works. Keys are encrypted and never shown again.
          </DialogDescription>
        </DialogHeader>

        {!presetKey ? (
          <div className="grid max-h-[60vh] gap-2 overflow-y-auto sm:grid-cols-2">
            {presets.data?.map((p) => (
              <button
                key={p.key}
                type="button"
                onClick={() => choose(p.key)}
                className="flex flex-col items-start gap-1 rounded-md border p-3 text-left text-sm hover:bg-accent"
              >
                <span className="font-medium">{p.display_name}</span>
                <span className="flex flex-wrap gap-1">
                  {p.free_tier && <Badge variant="secondary">free tier</Badge>}
                  {p.kind === "SERVER_LOCAL" && <Badge variant="outline">self-hosted</Badge>}
                  {p.trains_on_data === true && <Badge variant="outline">may train on data</Badge>}
                </span>
                {p.notes && <span className="text-xs text-muted-foreground">{p.notes}</span>}
              </button>
            ))}
            <button
              type="button"
              onClick={() => choose(CUSTOM)}
              className="flex flex-col items-start gap-1 rounded-md border border-dashed p-3 text-left text-sm hover:bg-accent"
            >
              <span className="font-medium">Custom OpenAI-compatible endpoint</span>
              <span className="text-xs text-muted-foreground">
                Your own vLLM/TGI server, a gateway, or any other provider.
              </span>
            </button>
          </div>
        ) : (
          <form
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              save.mutate(undefined, { onSuccess: () => setOpen(false) });
            }}
          >
            {preset?.kind === "SERVER_LOCAL" && (
              <Alert>
                <AlertDescription>
                  Self-hosted servers must be reachable from this server (self-host mode). For a model on your own PC or
                  a pendrive, use the Local Model Bridge instead.
                </AlertDescription>
              </Alert>
            )}
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="flex flex-col gap-2">
                <Label htmlFor="m-name">Display name</Label>
                <Input id="m-name" value={name} onChange={(e) => setName(e.target.value)} required />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="m-url">Base URL</Label>
                <Input
                  id="m-url"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="https://…/v1"
                  required
                />
              </div>
              <div className="flex flex-col gap-2 sm:col-span-2">
                <Label htmlFor="m-key">
                  API key{" "}
                  {preset?.signup_url && (
                    <a
                      href={preset.signup_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-xs font-normal underline"
                    >
                      get one
                    </a>
                  )}
                </Label>
                <Input
                  id="m-key"
                  type="password"
                  autoComplete="off"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={preset && !preset.requires_api_key ? "Not needed" : "Paste the key"}
                />
              </div>
              <div className="flex flex-col gap-2 sm:col-span-2">
                <Label htmlFor="m-model">Model</Label>
                <div className="flex gap-2">
                  <Input
                    id="m-model"
                    list="m-models"
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                    required
                  />
                  <datalist id="m-models">
                    {[...models, ...(preset?.model_hints ?? [])].map((m) => (
                      <option key={m} value={m} />
                    ))}
                  </datalist>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={discover.isPending || !baseUrl}
                    onClick={() =>
                      discover.mutate(undefined, {
                        onSuccess: (r) => {
                          setModels(r.models);
                          toast.success(`${r.models.length} models found`);
                        },
                      })
                    }
                  >
                    {discover.isPending ? "Loading…" : "Load models"}
                  </Button>
                </div>
                {models.length > 0 && (
                  <NativeSelect aria-label="Discovered models" value={model} onChange={(e) => setModel(e.target.value)}>
                    {models.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </NativeSelect>
                )}
              </div>
            </div>
            {canShare && (
              <div className="flex flex-col gap-3 rounded-md border p-3 text-sm">
                <div className="flex items-center justify-between gap-4">
                  <Label htmlFor="m-scope">Share with the whole organization</Label>
                  <Switch
                    id="m-scope"
                    checked={scope === "org"}
                    onCheckedChange={(c) => setScope(c ? "org" : "personal")}
                  />
                </div>
                <div className="flex items-center justify-between gap-4">
                  <Label htmlFor="m-training" className="font-normal">
                    Our agreement with this provider forbids training on our data
                  </Label>
                  <Switch id="m-training" checked={noTraining} onCheckedChange={setNoTraining} />
                </div>
              </div>
            )}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setPresetKey(null)}>
                Back
              </Button>
              <Button type="submit" disabled={save.isPending || !name || !baseUrl || !model}>
                {save.isPending ? "Saving and testing…" : "Save and test"}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
