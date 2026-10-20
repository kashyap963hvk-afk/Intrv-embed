"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import { probeProfile, useLlmMutation, type Profile } from "@/lib/llm-api";

const TIER_LABEL = { EXCELLENT: "Excellent", GOOD: "Good", USABLE: "Usable", BASIC: "Basic" } as const;
const STATUS_VARIANT = { OK: "success", DEGRADED: "outline", UNTESTED: "outline", ERROR: "destructive" } as const;
const CAPABILITIES = [
  ["stream", "Streaming"],
  ["json_schema", "JSON schema"],
  ["json_object", "JSON mode"],
  ["tools", "Tools"],
  ["vision", "Vision"],
] as const;

function TryDialog({ profile }: { profile: Profile }) {
  const [message, setMessage] = useState("Ask me one interview question about interrupt latency on a Cortex-M MCU.");
  const send = useLlmMutation(async () =>
    unwrap(
      await api.POST("/api/llm/profiles/{profile_id}/try", {
        params: { path: { profile_id: profile.id } },
        body: { message },
      }),
    ),
  );
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline">
          Try it
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Try {profile.name}</DialogTitle>
          <DialogDescription>One message, no history. Uses your request budget for this model.</DialogDescription>
        </DialogHeader>
        <Textarea aria-label="Message" rows={3} value={message} onChange={(e) => setMessage(e.target.value)} />
        <Button className="w-fit" disabled={send.isPending || !message.trim()} onClick={() => send.mutate(undefined)}>
          {send.isPending ? "Waiting…" : "Send"}
        </Button>
        {send.data && (
          <div className="flex flex-col gap-2 rounded-md bg-muted p-3 text-sm">
            <p className="whitespace-pre-wrap">{send.data.text}</p>
            <p className="text-xs text-muted-foreground">
              {send.data.ttft_ms != null && `First token ${send.data.ttft_ms} ms · `}
              {send.data.tokens_in} tokens in, {send.data.tokens_out} out
            </p>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function ProfileCard({ profile, canEdit }: { profile: Profile; canEdit: boolean }) {
  const caps = profile.capabilities as Record<string, unknown>;
  const warnings = (caps.warnings as string[] | undefined) ?? [];
  const probe = useLlmMutation(
    () => probeProfile(profile.id),
    (p) =>
      p.status === "OK"
        ? `${p.name}: rated ${p.quality_tier ? TIER_LABEL[p.quality_tier] : "untested"}`
        : `${p.name}: test failed`,
  );
  const remove = useLlmMutation(
    async () =>
      unwrap(await api.DELETE("/api/llm/profiles/{profile_id}", { params: { path: { profile_id: profile.id } } })),
    `${profile.name} removed`,
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          {profile.name}
          <Badge variant={STATUS_VARIANT[profile.status]}>{profile.status.toLowerCase()}</Badge>
          {profile.quality_tier && <Badge variant="secondary">{TIER_LABEL[profile.quality_tier]}</Badge>}
          <Badge variant="outline">{profile.scope === "org" ? "organization" : "personal"}</Badge>
        </CardTitle>
        <CardDescription className="font-mono text-xs break-all">
          {profile.model} · {profile.preset_key ?? profile.kind.toLowerCase()}
          {profile.has_api_key && ` · key ${profile.api_key_last4 ? `…${profile.api_key_last4}` : "set"}`}
        </CardDescription>
        {canEdit && (
          <CardAction>
            <Button
              size="sm"
              variant="ghost"
              className="text-destructive"
              onClick={() => window.confirm(`Remove ${profile.name}?`) && remove.mutate(undefined)}
            >
              Remove
            </Button>
          </CardAction>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        {profile.status !== "UNTESTED" && (
          <div className="flex flex-wrap gap-1">
            {CAPABILITIES.map(([key, label]) => (
              <Badge
                key={key}
                variant={caps[key] ? "secondary" : "outline"}
                className={caps[key] ? "" : "line-through opacity-60"}
              >
                {label}
              </Badge>
            ))}
            {typeof caps.ttft_ms === "number" && <Badge variant="outline">first token {caps.ttft_ms} ms</Badge>}
            {typeof caps.tokens_per_s === "number" && <Badge variant="outline">{caps.tokens_per_s} tok/s</Badge>}
          </div>
        )}
        {profile.trains_on_data !== false && (
          <p className="text-xs text-muted-foreground">
            {profile.trains_on_data ? "May train on your data" : "Data policy unknown"}: blocked from candidate data by
            default.
          </p>
        )}
        {profile.last_error && <p className="text-xs text-destructive">{profile.last_error}</p>}
        {warnings.length > 0 && (
          <ul className="list-disc pl-5 text-xs text-muted-foreground">
            {warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" disabled={probe.isPending} onClick={() => probe.mutate(undefined)}>
            {probe.isPending
              ? "Testing… (up to a minute)"
              : profile.status === "UNTESTED"
                ? "Test connection"
                : "Test again"}
          </Button>
          <TryDialog profile={profile} />
        </div>
      </CardContent>
    </Card>
  );
}
