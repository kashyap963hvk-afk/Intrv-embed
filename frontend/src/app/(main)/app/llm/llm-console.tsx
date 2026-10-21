"use client";

import { RequireAuth } from "@/components/auth/require-auth";
import { isOwner } from "@/lib/auth";
import { useProfiles } from "@/lib/llm-api";

import { AddModelDialog } from "./add-model-dialog";
import { LocalModelsPanel } from "./local-models-panel";
import { ProfileCard } from "./profile-card";
import { RoutingEditor } from "./routing-editor";

export function LlmConsole() {
  const profiles = useProfiles();
  return (
    <RequireAuth>
      {(me) => {
        const owner = isOwner(me);
        const canAdd = owner || me.policy.can_create_llm_profiles;
        return (
          <section className="flex flex-col gap-8">
            <div className="flex flex-wrap items-end justify-between gap-4">
              <div className="flex flex-col gap-1">
                <h1 className="text-2xl font-semibold tracking-tight">AI models</h1>
                <p className="max-w-2xl text-sm text-muted-foreground">
                  Plug in any OpenAI-compatible API (
                  {owner ? "for yourself or your whole organization" : "for yourself"}) or a model on your own PC, test
                  what it can really do, and choose which model handles each part of an interview.
                </p>
              </div>
              {canAdd ? (
                <AddModelDialog canShare={owner} />
              ) : (
                <p className="text-sm text-muted-foreground">Your organization manages models for you.</p>
              )}
            </div>

            {profiles.isError && <p className="text-sm text-destructive">{profiles.error.message}</p>}
            {profiles.data?.length === 0 && (
              <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
                No models yet. Add one to start generating questions and running interviews. A free key from Google AI
                Studio or Groq works for trying things out; use a paid or local model for real candidate data.
              </p>
            )}
            <div className="grid gap-4 lg:grid-cols-2">
              {profiles.data?.map((profile) => (
                <ProfileCard
                  key={profile.id}
                  profile={profile}
                  canEdit={profile.owned_by_me || (profile.scope === "org" && owner)}
                />
              ))}
            </div>

            <LocalModelsPanel profiles={profiles.data ?? []} canAdd={canAdd} owner={owner} />

            {profiles.data && profiles.data.length > 0 && <RoutingEditor profiles={profiles.data} isOwner={owner} />}
          </section>
        );
      }}
    </RequireAuth>
  );
}
