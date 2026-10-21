"use client";

import { useQuery } from "@tanstack/react-query";
import { Check, Copy, Cpu, HardDrive, LoaderCircle, Plug, TriangleAlert, Unplug, X } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import {
  buildBridgeProfileIn,
  buildGgufProfileIn,
  checkLabel,
  deviceVersion,
  EMPTY_GGUF_FORM,
  formatBytes,
  formatCountdown,
  formatMs,
  ggufFormProblem,
  hardwareNotes,
  newDevices,
  pairCommand,
  pairingDeadline,
  parseBridgeModels,
  parseProbe,
  profileForModel,
  profilesForDevice,
  secondsLeft,
  sourceLabel,
  TIER_LABEL,
  weakJson,
  type BridgeDevice,
  type BridgeModel,
  type GgufForm,
  type LlmProfile,
} from "@/lib/bridge";
import { useBridgeDevices, useCreateLocalProfile, useCreatePairingCode, useRevokeDevice } from "@/lib/bridge-api";
import { formatRelative } from "@/lib/connectors";
import { env } from "@/lib/env";
import { probeProfile, useLlmMutation } from "@/lib/llm-api";

const STATUS_VARIANT = { OK: "success", DEGRADED: "outline", UNTESTED: "outline", ERROR: "destructive" } as const;

/** The current time, refreshed every `everyMs` (countdowns and "last seen" labels). */
function useNow(everyMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(timer);
  }, [everyMs]);
  return now;
}

function CommandLine({ command, label }: { command: string; label: string }) {
  return (
    <div className="flex items-start gap-2 rounded-md bg-muted px-3 py-2">
      <code className="min-w-0 flex-1 font-mono text-xs break-all whitespace-pre-wrap">{command}</code>
      <Button
        type="button"
        size="icon"
        variant="ghost"
        className="size-6 shrink-0"
        aria-label={`Copy: ${label}`}
        onClick={() =>
          void navigator.clipboard.writeText(command).then(
            () => toast.success("Copied"),
            () => toast.error("Copying failed; select the text instead."),
          )
        }
      >
        <Copy className="size-3.5" aria-hidden />
      </Button>
    </div>
  );
}

function InstallSteps() {
  return (
    <details className="rounded-md border p-3 text-sm">
      <summary className="cursor-pointer font-medium">1. Get the bridge app on your PC</summary>
      <div className="mt-3 flex flex-col gap-3">
        <p className="text-muted-foreground">
          The bridge (<span className="font-mono">intrv-bridge</span>) ships in the{" "}
          <span className="font-mono">bridge/</span> folder of the Intrv-embed source; ask your administrator for a
          copy. It needs no administrator rights and opens no port on your PC.
        </p>
        <div className="flex flex-col gap-1.5">
          <span className="font-medium">With Python 3.11+ and uv (run from the folder that contains bridge/)</span>
          <CommandLine label="run with uvx" command="uvx --from ./bridge intrv-bridge --help" />
          <span className="text-xs text-muted-foreground">
            Or install it once with <span className="font-mono">pipx install ./bridge</span>. Without installing, put{" "}
            <span className="font-mono">uvx --from ./bridge</span> in front of each command below.
          </span>
        </div>
        <div className="flex flex-col gap-1.5">
          <span className="font-medium">As a single-file program for Windows, macOS or Linux</span>
          <CommandLine
            label="build with PyInstaller"
            command="cd bridge && uv sync --group dev && uv run --with pyinstaller pyinstaller packaging/intrv-bridge.spec"
          />
          <span className="text-xs text-muted-foreground">
            Build it on the operating system you want to run it on; the program lands in{" "}
            <span className="font-mono">bridge/dist/</span>.
          </span>
        </div>
      </div>
    </details>
  );
}

interface Pairing {
  code: string;
  deadline: number;
  total: number;
  known: ReadonlySet<string>;
}

function PairingStep({
  devices,
  pairing,
  onPairing,
}: {
  devices: readonly BridgeDevice[];
  pairing: Pairing | null;
  onPairing: (next: Pairing | null) => void;
}) {
  const [deviceName, setDeviceName] = useState("My PC");
  const create = useCreatePairingCode();
  const now = useNow(1_000);
  const added = pairing ? newDevices(pairing.known, devices) : [];
  const left = pairing ? secondsLeft(pairing.deadline, now, pairing.total) : 0;

  function start() {
    create.mutate(undefined, {
      onSuccess: (result) =>
        onPairing({
          code: result.code,
          deadline: pairingDeadline(Date.now(), result.expires_in_s),
          total: result.expires_in_s,
          known: new Set(devices.map((d) => d.id)),
        }),
    });
  }

  return (
    <div className="flex flex-col gap-3 rounded-md border p-3 text-sm">
      <h3 className="font-medium">2. Pair your PC</h3>
      {added.length > 0 ? (
        <Alert>
          <Check aria-hidden />
          <AlertTitle>{added.map((d) => d.name).join(", ")} paired</AlertTitle>
          <AlertDescription>
            <p>
              Keep <span className="font-mono">intrv-bridge run</span> open on that PC. Its models appear below once it
              connects; add one and test it.
            </p>
            <Button size="sm" variant="outline" onClick={() => onPairing(null)}>
              Done
            </Button>
          </AlertDescription>
        </Alert>
      ) : pairing && left > 0 ? (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-4">
            <span
              className="rounded-md border-2 border-primary/60 px-3 py-1 font-mono text-3xl tracking-[0.3em] tabular-nums"
              aria-label={`Pairing code ${pairing.code.split("").join(" ")}`}
            >
              {pairing.code}
            </span>
            <span className="text-muted-foreground tabular-nums" aria-live="off">
              Expires in {formatCountdown(left)} · single use
            </span>
            <Button size="sm" variant="ghost" onClick={() => onPairing(null)}>
              <X aria-hidden /> Cancel
            </Button>
          </div>
          <div className="flex flex-col gap-1.5">
            <CommandLine label="pair" command={pairCommand(env.apiBaseUrl, pairing.code, deviceName)} />
            <CommandLine label="add a model folder" command="intrv-bridge add-folder E:\models" />
            <CommandLine label="install llama-server" command="intrv-bridge install-llama-server" />
            <CommandLine label="run" command="intrv-bridge run" />
          </div>
          <ul className="list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">
            <li>
              <span className="font-mono">add-folder</span>: any folder or pendrive with .gguf files. Models already
              running in Ollama or LM Studio are found without it.
            </li>
            <li>
              <span className="font-mono">install-llama-server</span>: only for .gguf files. Downloads a llama.cpp CPU
              build and checks its SHA-256; with a GPU, set <span className="font-mono">llama_server</span> in the
              bridge&apos;s config file to your own CUDA, Metal or Vulkan build.
            </li>
          </ul>
          <p className="flex items-center gap-2 text-muted-foreground" role="status">
            <LoaderCircle className="size-4 animate-spin" aria-hidden /> Waiting for your PC to use the code…
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {pairing && <p className="text-destructive">That code expired before a PC used it. Create a new one.</p>}
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="bridge-name">Name for this PC</Label>
              <Input
                id="bridge-name"
                className="w-56"
                maxLength={60}
                value={deviceName}
                onChange={(e) => setDeviceName(e.target.value)}
              />
            </div>
            <Button disabled={create.isPending} onClick={start}>
              <Plug aria-hidden /> {create.isPending ? "Creating…" : "Create pairing code"}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            You get a 6-digit code, valid for 10 minutes and usable once. The bridge exchanges it for its own device
            key; you can revoke the PC here at any time.
          </p>
        </div>
      )}
    </div>
  );
}

function ProbeResult({ profile }: { profile: LlmProfile }) {
  const probe = parseProbe(profile.capabilities);
  const checks = Object.entries(probe.checks);
  const weak = profile.quality_tier === "BASIC" || profile.quality_tier === "USABLE" || weakJson(probe);
  return (
    <div className="flex flex-col gap-2 text-xs">
      <div className="flex flex-wrap gap-1">
        {probe.ttft_ms !== null && <Badge variant="outline">first token {formatMs(probe.ttft_ms)}</Badge>}
        {probe.tokens_per_s !== null && <Badge variant="outline">{probe.tokens_per_s} tokens/s</Badge>}
        <Badge variant={weakJson(probe) ? "outline" : "secondary"}>
          {weakJson(probe) ? "no native JSON mode" : "JSON mode"}
        </Badge>
        {probe.vision && <Badge variant="secondary">vision</Badge>}
      </div>
      {checks.length > 0 && (
        <ul className="grid gap-0.5 sm:grid-cols-2">
          {checks.map(([key, passed]) => (
            <li key={key} className="flex items-center gap-1.5">
              {passed ? (
                <Check className="size-3.5 text-success" aria-label="passed" />
              ) : (
                <X className="size-3.5 text-destructive" aria-label="failed" />
              )}
              {checkLabel(key)}
            </li>
          ))}
        </ul>
      )}
      {probe.warnings.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-5 text-muted-foreground">
          {probe.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      {weak && (
        <p className="rounded-md bg-muted p-2">
          Small local models are often slow and unreliable at structured JSON. The platform repairs and validates every
          JSON answer and, if a model still fails, moves on to the next model in that step&apos;s routing chain, so put
          a stronger model behind this one for the turn controller and the grader.
        </p>
      )}
    </div>
  );
}

const OFFLINE_HINT = "Start the bridge on this PC first";

function TestingNote() {
  return (
    <p className="flex items-center gap-2 text-xs text-muted-foreground" role="status">
      <LoaderCircle className="size-3.5 animate-spin" aria-hidden />
      Testing on your PC. Loading a model from a pendrive can take a few minutes; keep the bridge running.
    </p>
  );
}

function ProfileStatus({ profile, online }: { profile: LlmProfile; online: boolean }) {
  const probe = useLlmMutation(
    () => probeProfile(profile.id),
    (p) =>
      p.status === "OK"
        ? `${p.name}: rated ${p.quality_tier ? TIER_LABEL[p.quality_tier] : "untested"}`
        : `${p.name}: test failed`,
  );
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={STATUS_VARIANT[profile.status]}>{profile.status.toLowerCase()}</Badge>
        {profile.quality_tier && <Badge variant="secondary">{TIER_LABEL[profile.quality_tier]}</Badge>}
        <Button
          size="sm"
          variant="outline"
          disabled={probe.isPending || !online}
          title={online ? undefined : OFFLINE_HINT}
          onClick={() => probe.mutate(undefined)}
        >
          {probe.isPending ? "Testing…" : profile.status === "UNTESTED" ? "Test" : "Test again"}
        </Button>
      </div>
      {probe.isPending && <TestingNote />}
      {profile.last_error && <p className="text-xs text-destructive">{profile.last_error}</p>}
      {profile.status !== "UNTESTED" && <ProbeResult profile={profile} />}
    </>
  );
}

function ModelRow({
  device,
  model,
  profile,
  canAdd,
}: {
  device: BridgeDevice;
  model: BridgeModel;
  profile: LlmProfile | undefined;
  canAdd: boolean;
}) {
  const add = useCreateLocalProfile();
  const facts = [
    sourceLabel(model.source),
    formatBytes(model.size_bytes),
    model.quant,
    model.arch,
    model.context_length ? `${model.context_length.toLocaleString()} tokens context` : null,
  ].filter(Boolean);

  return (
    <li className="flex flex-col gap-2 rounded-md border p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="font-medium break-all">{model.name}</span>
          <span className="text-xs text-muted-foreground">{facts.join(" · ")}</span>
        </div>
        {!profile && canAdd && (
          <Button
            size="sm"
            disabled={add.isPending || !device.online}
            title={device.online ? undefined : OFFLINE_HINT}
            onClick={() => add.mutate(buildBridgeProfileIn(device, model))}
          >
            {add.isPending ? "Adding and testing…" : "Add and test"}
          </Button>
        )}
      </div>
      {add.isPending && <TestingNote />}
      {profile && <ProfileStatus profile={profile} online={device.online} />}
      <details className="text-xs">
        <summary className="cursor-pointer text-muted-foreground">What to expect on your hardware</summary>
        <ul className="mt-1 list-disc space-y-0.5 pl-5 text-muted-foreground">
          {hardwareNotes(model).map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      </details>
    </li>
  );
}

function DeviceCard({
  device,
  profiles,
  canAdd,
  now,
}: {
  device: BridgeDevice;
  profiles: readonly LlmProfile[];
  canAdd: boolean;
  now: number;
}) {
  const revoke = useRevokeDevice();
  const models = parseBridgeModels(device.models);
  const version = deviceVersion(device);
  const linked = profilesForDevice(profiles, device.id);
  const seen = formatRelative(device.last_seen_at, now);

  function confirmRevoke() {
    const affected = linked.length
      ? ` ${linked.length} model profile${linked.length === 1 ? "" : "s"} using it will stop working until you remove ${linked.length === 1 ? "it" : "them"}.`
      : "";
    if (window.confirm(`Revoke ${device.name}? The bridge disconnects at once and must be paired again.${affected}`)) {
      revoke.mutate({ id: device.id, name: device.name });
    }
  }

  return (
    <Card className="gap-3 py-4">
      <CardHeader className="px-4">
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          <HardDrive className="size-4" aria-hidden />
          {device.name}
          {device.online ? (
            <Badge variant="success">online</Badge>
          ) : (
            <Badge variant="outline">
              <Unplug aria-hidden /> offline
            </Badge>
          )}
        </CardTitle>
        <CardDescription className="text-xs">
          {[
            device.platform || null,
            version ? `bridge ${version}` : null,
            device.online ? "connected now" : seen ? `last seen ${seen}` : "never connected",
            `paired ${new Date(device.created_at).toLocaleDateString()}`,
          ]
            .filter(Boolean)
            .join(" · ")}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 px-4 text-sm">
        {!device.online && (
          <p className="text-xs text-muted-foreground">
            Run <span className="font-mono">intrv-bridge run</span> on this PC. While it is offline, interviews use the
            next model in each routing chain, if you set one.
          </p>
        )}
        {models.length > 0 ? (
          <ul className="flex flex-col gap-2">
            {models.map((model) => (
              <ModelRow
                key={model.id}
                device={device}
                model={model}
                profile={profileForModel(profiles, device.id, model.id)}
                canAdd={canAdd}
              />
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted-foreground">
            {device.last_seen_at
              ? "No models found on this PC. Add a folder with .gguf files (intrv-bridge add-folder) or start Ollama or LM Studio, then restart the bridge."
              : "Models appear here once the bridge connects."}
          </p>
        )}
        <Button
          size="sm"
          variant="ghost"
          className="w-fit text-destructive"
          disabled={revoke.isPending}
          onClick={confirmRevoke}
        >
          Revoke this PC
        </Button>
      </CardContent>
    </Card>
  );
}

function ServerGgufForm({ policyAllows }: { policyAllows: boolean | null }) {
  const [form, setForm] = useState<GgufForm>(EMPTY_GGUF_FORM);
  const create = useCreateLocalProfile();
  const problem = ggufFormProblem(form);
  return (
    <details className="rounded-md border p-3 text-sm">
      <summary className="cursor-pointer font-medium">Self-host: run a .gguf file on this server (owners)</summary>
      <form
        className="mt-3 flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (problem) return;
          create.mutate(buildGgufProfileIn(form), { onSuccess: () => setForm(EMPTY_GGUF_FORM) });
        }}
      >
        <p className="text-muted-foreground">
          Only for self-hosted deployments. The server runs llama.cpp on the file itself, and only opens files inside
          the folders in its <span className="font-mono">GGUF_ALLOWED_DIRS</span> setting (symlinks are followed and
          checked too). Cloud deployments have this switched off and refuse the path; use the bridge above instead.
        </p>
        {policyAllows === false && (
          <p className="text-destructive">
            Your organization&apos;s AI policy does not allow server-run model files. Enable them in Administration → AI
            policy first.
          </p>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5 sm:col-span-2">
            <Label htmlFor="gguf-path">Absolute path on the server</Label>
            <Input
              id="gguf-path"
              className="font-mono"
              placeholder="/models/qwen2.5-14b-instruct-q4_k_m.gguf"
              value={form.path}
              maxLength={1000}
              onChange={(e) => setForm({ ...form, path: e.target.value })}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="gguf-name">Display name (optional)</Label>
            <Input
              id="gguf-name"
              value={form.name}
              maxLength={120}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </div>
          <div className="flex items-end gap-2 pb-2">
            <Switch
              id="gguf-org"
              checked={form.shareWithOrg}
              onCheckedChange={(c) => setForm({ ...form, shareWithOrg: c })}
            />
            <Label htmlFor="gguf-org">Share with the whole organization</Label>
          </div>
        </div>
        {form.path && problem && <p className="text-destructive">{problem}</p>}
        <Button type="submit" className="w-fit" disabled={Boolean(problem) || create.isPending}>
          {create.isPending ? "Saving and testing…" : "Save and test"}
        </Button>
      </form>
    </details>
  );
}

export function LocalModelsPanel({
  profiles,
  canAdd,
  owner,
}: {
  profiles: readonly LlmProfile[];
  canAdd: boolean;
  owner: boolean;
}) {
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const devices = useBridgeDevices(pairing !== null);
  const now = useNow(30_000);
  const policy = useQuery({
    queryKey: ["admin", "org-policy"],
    queryFn: async () => unwrap(await api.GET("/api/admin/org-policy")),
    enabled: owner,
  });
  const kinds = policy.data?.allowed_provider_kinds;
  const bridgeAllowed = kinds ? kinds.includes("BRIDGE") : null;
  const ggufAllowed = kinds ? kinds.includes("GGUF_MANAGED") : null;
  const list = devices.data ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Cpu className="size-5" aria-hidden /> Use a model on my PC or pendrive
        </CardTitle>
        <CardDescription className="max-w-3xl">
          The Local Model Bridge is a small app for your own computer. It serves a .gguf model from your disk or a USB
          stick (with llama.cpp), or a model already running in Ollama, LM Studio or vLLM. Your PC connects out to this
          platform, so nothing on your network has to accept connections, and the platform can only ask it for chat
          answers and its model list. Candidate data sent to it stays on your PC.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {bridgeAllowed === false && (
          <Alert>
            <TriangleAlert aria-hidden />
            <AlertTitle>Blocked by your AI policy</AlertTitle>
            <AlertDescription>
              Models on members&apos; own PCs are not allowed in your organization. Allow them in Administration → AI
              policy, or pairing works but adding a model fails.
            </AlertDescription>
          </Alert>
        )}
        {!canAdd && (
          <p className="text-sm text-muted-foreground">
            You can pair a PC, but your organization does not let you add your own models, so you cannot use it yet.
          </p>
        )}
        <InstallSteps />
        <PairingStep devices={list} pairing={pairing} onPairing={setPairing} />
        <div className="flex flex-col gap-3">
          <h3 className="text-sm font-medium">3. Your paired PCs</h3>
          {devices.isError && <p className="text-sm text-destructive">{devices.error.message}</p>}
          {devices.isPending && <p className="text-sm text-muted-foreground">Loading…</p>}
          {devices.data?.length === 0 && (
            <p className="text-sm text-muted-foreground">No PC is paired yet. Create a pairing code above.</p>
          )}
          <div className="grid gap-3 xl:grid-cols-2">
            {list.map((device) => (
              <DeviceCard key={device.id} device={device} profiles={profiles} canAdd={canAdd} now={now} />
            ))}
          </div>
        </div>
        {owner && <ServerGgufForm policyAllows={ggufAllowed} />}
      </CardContent>
    </Card>
  );
}
