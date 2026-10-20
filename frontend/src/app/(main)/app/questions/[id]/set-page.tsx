"use client";

import { ArrowLeft, Download, LoaderCircle, Pencil, RotateCw, Trash } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

import { RequireAuth } from "@/components/auth/require-auth";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useCvProfile } from "@/lib/cv-api";
import { EXPORT_FORMATS, moveItem, parseAnalysis, parseItemData, type QuestionSetDetail } from "@/lib/questions";
import {
  useDeleteSet,
  useExportSet,
  useQuestionSet,
  useQuestionSetEvents,
  useRenameSet,
  useReorderItems,
  useRetrySet,
} from "@/lib/questions-api";
import { useRolePacks } from "@/lib/rolepacks-api";

import { SetStatusBadge } from "../set-status-badge";
import { AnalysisPanel } from "./analysis-panel";
import { ProfilePanel } from "./profile-panel";
import { QuestionCard } from "./question-card";

function TitleEditor({ set }: { set: QuestionSetDetail }) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(set.title);
  const rename = useRenameSet(set.id);

  if (!editing) {
    return (
      <h1 className="flex flex-wrap items-center gap-2 text-2xl font-semibold tracking-tight">
        {set.title}
        <Button
          size="icon"
          variant="ghost"
          aria-label="Rename"
          title="Rename"
          onClick={() => {
            setTitle(set.title);
            setEditing(true);
          }}
        >
          <Pencil />
        </Button>
        <SetStatusBadge status={set.status} />
      </h1>
    );
  }
  return (
    <form
      className="flex flex-wrap items-center gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        rename.mutate(title.trim(), { onSuccess: () => setEditing(false) });
      }}
    >
      <Input
        aria-label="Title"
        className="w-96 max-w-full"
        value={title}
        maxLength={200}
        autoFocus
        onChange={(e) => setTitle(e.target.value)}
      />
      <Button type="submit" size="sm" disabled={!title.trim() || rename.isPending}>
        {rename.isPending ? "Saving…" : "Save"}
      </Button>
      <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)}>
        Cancel
      </Button>
    </form>
  );
}

function SetHeader({ set }: { set: QuestionSetDetail }) {
  const packs = useRolePacks();
  const exportSet = useExportSet();
  const remove = useDeleteSet();
  const router = useRouter();
  const pack = packs.data?.find((p) => p.id === set.role_pack_id);
  const variant = pack?.variants.find((v) => v.id === set.role_variant);

  return (
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div className="flex min-w-0 flex-col gap-1">
        <Link href="/app/questions" className="flex w-fit items-center gap-1 text-sm text-muted-foreground">
          <ArrowLeft className="size-4" aria-hidden /> Questions
        </Link>
        <TitleEditor key={set.title} set={set} />
        <p className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          {pack ? (
            <Link href={`/app/role-packs/${pack.id}`} className="hover:underline">
              {pack.name}
            </Link>
          ) : (
            "Role pack unavailable"
          )}
          {variant && <span>· {variant.name}</span>}
          {set.role_pack_version !== null && <Badge variant="outline">pack v{set.role_pack_version}</Badge>}
          <span>· {new Date(set.created_at).toLocaleString()}</span>
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        {set.status === "READY" &&
          EXPORT_FORMATS.map(({ format, label }) => (
            <Button
              key={format}
              size="sm"
              variant="outline"
              disabled={exportSet.isPending}
              title={`Download the interviewer copy (with model answers) as ${label}`}
              onClick={() => exportSet.mutate({ set, format })}
            >
              <Download /> {label}
            </Button>
          ))}
        <Button
          size="sm"
          variant="outline"
          className="text-destructive"
          disabled={remove.isPending}
          onClick={() =>
            window.confirm(`Delete ${set.title} and all its questions?`) &&
            remove.mutate(set, { onSuccess: () => router.push("/app/questions") })
          }
        >
          <Trash /> Delete
        </Button>
      </div>
    </div>
  );
}

function ReadySet({ set }: { set: QuestionSetDetail }) {
  const profile = useCvProfile(set.cv_document_id);
  const reorder = useReorderItems(set.id);
  const evidenceIndex = profile.data?.evidence_index ?? null;
  const analysis = parseAnalysis(set.analysis);
  const ids = set.items.map((item) => item.id);
  const pinned = set.items.filter((item) => item.pinned).length;

  return (
    <div className="flex flex-col gap-6">
      <div className="grid gap-6 lg:grid-cols-2">
        <ProfilePanel profile={profile.data ?? null} loading={profile.isPending && set.cv_document_id !== null} />
        <AnalysisPanel analysis={analysis} competencyNames={set.competency_names} evidenceIndex={evidenceIndex} />
      </div>
      <section className="flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <h2 className="text-lg font-semibold">
            {set.items.length} {set.items.length === 1 ? "question" : "questions"}
          </h2>
          <p className="text-sm text-muted-foreground">
            Click a question to edit it. Pin the ones to keep{pinned > 0 ? ` (${pinned} pinned)` : ""}; regenerate the
            rest one at a time, optionally with a hint.
          </p>
        </div>
        {set.items.length === 0 ? (
          <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
            No questions left in this set.
          </p>
        ) : (
          <ol className="flex flex-col gap-3">
            {set.items.map((item, position) => (
              <QuestionCard
                key={item.id}
                setId={set.id}
                item={item}
                data={parseItemData(item.data)}
                position={position}
                total={set.items.length}
                competencyNames={set.competency_names}
                citationTitles={set.citation_titles}
                evidenceIndex={evidenceIndex}
                moving={reorder.isPending}
                onMove={(delta) => {
                  const next = moveItem(ids, position, delta);
                  if (next) reorder.mutate(next);
                }}
              />
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}

function SetWorkspace() {
  const { id } = useParams<{ id: string }>();
  const set = useQuestionSet(id);
  const retry = useRetrySet(id);
  useQuestionSetEvents();

  if (set.isPending) return <p className="text-sm text-muted-foreground">Loading question set…</p>;
  if (set.isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Question set unavailable</AlertTitle>
        <AlertDescription>
          <p>{set.error.message}</p>
          <Link href="/app/questions" className={buttonVariants({ variant: "outline", size: "sm" })}>
            Back to questions
          </Link>
        </AlertDescription>
      </Alert>
    );
  }
  const detail = set.data;

  return (
    <section className="flex flex-col gap-6">
      <SetHeader set={detail} />
      {detail.status === "GENERATING" && (
        <Card>
          <CardContent className="flex flex-col gap-2">
            <p className="flex items-center gap-2 font-medium" role="status" aria-live="polite">
              <LoaderCircle className="size-4 animate-spin" aria-hidden />
              {detail.progress || "Working…"}
            </p>
            <p className="text-sm text-muted-foreground">
              Reading the CV, planning by competency weight, writing grounded questions and reviewing them. You can
              leave this page; generation continues and this page updates live.
            </p>
          </CardContent>
        </Card>
      )}
      {detail.status === "FAILED" && (
        <Alert variant="destructive">
          <AlertTitle>Generation failed</AlertTitle>
          <AlertDescription>
            <p>{detail.error ?? "The question set could not be generated."}</p>
            <Button size="sm" variant="outline" disabled={retry.isPending} onClick={() => retry.mutate(undefined)}>
              <RotateCw /> {retry.isPending ? "Retrying…" : "Retry"}
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {detail.status === "READY" && <ReadySet set={detail} />}
    </section>
  );
}

export function SetPage() {
  return <RequireAuth>{() => <SetWorkspace />}</RequireAuth>;
}
