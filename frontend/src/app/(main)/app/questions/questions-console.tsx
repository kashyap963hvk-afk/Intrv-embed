"use client";

import { Trash } from "lucide-react";
import Link from "next/link";

import { RequireAuth } from "@/components/auth/require-auth";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { QuestionSet } from "@/lib/questions";
import { useDeleteSet, useQuestionSetEvents, useQuestionSets } from "@/lib/questions-api";

import { NewSetDialog } from "./new-set-dialog";
import { SetStatusBadge } from "./set-status-badge";

function SetRow({ set }: { set: QuestionSet }) {
  const remove = useDeleteSet();
  return (
    <TableRow>
      <TableCell className="max-w-md">
        <div className="flex flex-col gap-0.5">
          <Link href={`/app/questions/${set.id}`} className="truncate font-medium hover:underline" title={set.title}>
            {set.title}
          </Link>
          {set.status === "FAILED" && set.error && (
            <span className="line-clamp-2 text-xs text-destructive">{set.error}</span>
          )}
        </div>
      </TableCell>
      <TableCell>
        <div className="flex flex-col gap-1">
          <SetStatusBadge status={set.status} />
          {set.status === "GENERATING" && set.progress && (
            <span className="text-xs text-muted-foreground">{set.progress}</span>
          )}
        </div>
      </TableCell>
      <TableCell className="text-sm tabular-nums">{set.status === "READY" ? set.item_count : "—"}</TableCell>
      <TableCell className="text-xs whitespace-nowrap text-muted-foreground">
        {new Date(set.created_at).toLocaleString()}
      </TableCell>
      <TableCell>
        <div className="flex justify-end">
          <Button
            size="icon"
            variant="ghost"
            className="text-destructive"
            aria-label={`Delete ${set.title}`}
            title="Delete"
            disabled={remove.isPending}
            onClick={() => window.confirm(`Delete ${set.title} and all its questions?`) && remove.mutate(set)}
          >
            <Trash />
          </Button>
        </div>
      </TableCell>
    </TableRow>
  );
}

function QuestionSets() {
  const sets = useQuestionSets();
  useQuestionSetEvents();

  return (
    <section className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-semibold tracking-tight">Questions</h1>
          <p className="max-w-2xl text-sm text-muted-foreground">
            Interview question sets built from a candidate&apos;s CV, a role pack and, optionally, the job description.
            Each question comes with its rationale, the CV evidence it probes and a model answer.
          </p>
        </div>
        <NewSetDialog />
      </div>

      {sets.isError && <p className="text-sm text-destructive">{sets.error.message}</p>}
      {sets.isPending && <p className="text-sm text-muted-foreground">Loading question sets…</p>}
      {sets.data &&
        (sets.data.length === 0 ? (
          <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
            No question sets yet. Use New question set to generate one from a CV.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Title</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Questions</TableHead>
                <TableHead>Created</TableHead>
                <TableHead className="text-right">
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sets.data.map((set) => (
                <SetRow key={set.id} set={set} />
              ))}
            </TableBody>
          </Table>
        ))}
    </section>
  );
}

export function QuestionsConsole() {
  return <RequireAuth>{() => <QuestionSets />}</RequireAuth>;
}
