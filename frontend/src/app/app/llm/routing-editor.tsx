"use client";

import { ArrowDown, ArrowUp, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { NativeSelect } from "@/components/ui/native-select";
import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import { AGENT_ROLES, useLlmMutation, useRoutes, type AgentRole, type Profile } from "@/lib/llm-api";

type Chains = Partial<Record<AgentRole, string[]>>;

export function RoutingEditor({ profiles, isOwner }: { profiles: Profile[]; isOwner: boolean }) {
  const routes = useRoutes();
  const [scope, setScope] = useState<"mine" | "org">("mine");

  return (
    <Card>
      <CardHeader>
        <CardTitle>Which model does what</CardTitle>
        <CardDescription>
          Each part of the interview uses the first model in its list and falls back to the next one if it fails or is
          rate-limited. Empty lists use the best available model automatically.
          {scope === "mine" && " Your choices override the organization default."}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {isOwner && (
          <NativeSelect
            aria-label="Routing scope"
            className="w-fit"
            value={scope}
            onChange={(e) => setScope(e.target.value as "mine" | "org")}
          >
            <option value="mine">My routing</option>
            <option value="org">Organization default</option>
          </NativeSelect>
        )}
        {routes.data && (
          <ChainsForm
            key={`${scope}-${routes.dataUpdatedAt}`}
            scope={scope}
            initial={scope === "org" ? routes.data.org : routes.data.mine}
            profiles={profiles}
          />
        )}
      </CardContent>
    </Card>
  );
}

function ChainsForm({ scope, initial, profiles }: { scope: "mine" | "org"; initial: Chains; profiles: Profile[] }) {
  const [chains, setChains] = useState<Chains>(() => ({ ...initial }));
  const names = new Map(profiles.map((p) => [p.id, p.name]));
  const usable = profiles.filter((p) => (scope === "org" ? p.scope === "org" : true));

  const save = useLlmMutation(
    async () =>
      unwrap(
        await api.PUT("/api/llm/routes", {
          body: {
            scope: scope === "org" ? "org" : "personal",
            routes: Object.fromEntries(AGENT_ROLES.map(({ role }) => [role, chains[role] ?? []])),
          },
        }),
      ),
    "Routing saved",
  );

  function update(role: AgentRole, next: string[]) {
    setChains((c) => ({ ...c, [role]: next }));
  }

  return (
    <>
      <div className="flex flex-col divide-y">
        {AGENT_ROLES.map(({ role, label, help }) => {
          const chain = chains[role] ?? [];
          return (
            <div key={role} className="grid gap-2 py-3 md:grid-cols-[220px_1fr]">
              <div>
                <div className="text-sm font-medium">{label}</div>
                <div className="text-xs text-muted-foreground">{help}</div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {chain.map((id, index) => (
                  <span key={id} className="flex items-center gap-1 rounded-md border px-2 py-1 text-xs">
                    <span className="text-muted-foreground">{index + 1}.</span> {names.get(id) ?? "removed model"}
                    <button
                      type="button"
                      aria-label="Move earlier"
                      disabled={index === 0}
                      onClick={() => {
                        const next = [...chain];
                        [next[index - 1], next[index]] = [next[index], next[index - 1]];
                        update(role, next);
                      }}
                    >
                      <ArrowUp className="size-3" />
                    </button>
                    <button
                      type="button"
                      aria-label="Move later"
                      disabled={index === chain.length - 1}
                      onClick={() => {
                        const next = [...chain];
                        [next[index + 1], next[index]] = [next[index], next[index + 1]];
                        update(role, next);
                      }}
                    >
                      <ArrowDown className="size-3" />
                    </button>
                    <button
                      type="button"
                      aria-label="Remove"
                      onClick={() =>
                        update(
                          role,
                          chain.filter((x) => x !== id),
                        )
                      }
                    >
                      <X className="size-3" />
                    </button>
                  </span>
                ))}
                <NativeSelect
                  aria-label={`Add a model for ${label}`}
                  className="h-8 text-xs"
                  value=""
                  onChange={(e) => e.target.value && update(role, [...chain, e.target.value])}
                >
                  <option value="">{chain.length ? "Add fallback…" : "Automatic (add a model…)"}</option>
                  {usable
                    .filter((p) => !chain.includes(p.id))
                    .map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                </NativeSelect>
              </div>
            </div>
          );
        })}
      </div>
      <Button className="w-fit" disabled={save.isPending} onClick={() => save.mutate(undefined)}>
        Save routing
      </Button>
    </>
  );
}
