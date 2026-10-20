"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useSyncExternalStore } from "react";

import { RequireAuth } from "@/components/auth/require-auth";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { decodeDraft, DRAFT_STORAGE_KEY } from "@/lib/rolepacks";
import { useCreatePack } from "@/lib/rolepacks-api";

import { SpecEditor, type EditorTab } from "../spec-editor";

function readDraft(): string | null {
  try {
    return sessionStorage.getItem(DRAFT_STORAGE_KEY);
  } catch {
    return null;
  }
}

function clearDraft() {
  try {
    sessionStorage.removeItem(DRAFT_STORAGE_KEY);
  } catch {
    // Storage disabled: nothing was stored either.
  }
}

// The draft is written before navigating here; no updates need to be observed while the page is open.
const subscribe = () => () => {};

function NewPackWorkspace() {
  const stored = useSyncExternalStore(subscribe, readDraft, () => null);
  const yaml = decodeDraft(stored);
  const create = useCreatePack();
  const router = useRouter();
  const [tab, setTab] = useState<EditorTab>("yaml");

  return (
    <section className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <Link href="/app/role-packs" className="flex w-fit items-center gap-1 text-sm text-muted-foreground">
          <ArrowLeft className="size-4" aria-hidden /> Role packs
        </Link>
        <h1 className="flex flex-wrap items-center gap-2 text-2xl font-semibold tracking-tight">
          New role pack <Badge variant="outline">draft, not saved</Badge>
        </h1>
        <p className="text-sm text-muted-foreground">
          A Role Builder draft. Fix any problems listed, then save it as your private pack.
        </p>
      </div>
      {yaml ? (
        <SpecEditor
          key={yaml}
          initialSpec={null}
          initialYaml={yaml}
          readOnly={false}
          tab={tab}
          onTabChange={setTab}
          saveLabel="Save as my pack"
          saving={create.isPending}
          saveError={create.error}
          savePristine
          onSave={(body, onSaved) =>
            create.mutate(body, {
              onSuccess: (pack) => {
                onSaved();
                clearDraft();
                router.replace(`/app/role-packs/${pack.id}`);
              },
            })
          }
        />
      ) : (
        <div className="flex flex-col items-start gap-3 rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
          <p>No draft to open. Drafts from Build with AI open here and are kept only in this browser tab.</p>
          <Link href="/app/role-packs" className={buttonVariants({ variant: "outline", size: "sm" })}>
            Back to role packs
          </Link>
        </div>
      )}
    </section>
  );
}

export function NewPack() {
  return <RequireAuth>{() => <NewPackWorkspace />}</RequireAuth>;
}
