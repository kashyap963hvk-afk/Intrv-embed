"use client";

import Link from "next/link";

import { RequireAuth } from "@/components/auth/require-auth";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { decisionLabel, decisionTone, recommendationLabel, recommendationTone, type Interview } from "@/lib/interviews";
import { useInterviewEvents, useInterviews } from "@/lib/interviews-api";

import { InterviewStatusBadge } from "./interview-status-badge";
import { NewInterviewDialog } from "./new-interview-dialog";
import { SectionNav } from "./section-nav";
import { ToneBadge } from "./tone-badge";

function Outcome({ interview }: { interview: Interview }) {
  if (interview.scorecard_status === "PENDING") {
    return <span className="text-xs text-muted-foreground">Grading…</span>;
  }
  if (interview.scorecard_status === "FAILED") {
    return <span className="text-xs text-destructive">Grading failed</span>;
  }
  if (!interview.recommendation && !interview.human_decision) return <span className="text-muted-foreground">—</span>;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {interview.recommendation && (
        <ToneBadge tone={recommendationTone(interview.recommendation)} title="AI recommendation (advisory)">
          AI: {recommendationLabel(interview.recommendation)}
        </ToneBadge>
      )}
      {interview.human_decision ? (
        <ToneBadge tone={decisionTone(interview.human_decision)} title="Human decision">
          {decisionLabel(interview.human_decision)}
        </ToneBadge>
      ) : (
        <span className="text-xs text-muted-foreground">Awaiting decision</span>
      )}
    </div>
  );
}

function InterviewRow({ interview }: { interview: Interview }) {
  return (
    <TableRow>
      <TableCell className="max-w-sm">
        <div className="flex flex-col gap-0.5">
          <Link
            href={`/app/interviews/${interview.id}`}
            className="truncate font-medium hover:underline"
            title={interview.title}
          >
            {interview.title}
          </Link>
          <span className="truncate text-xs text-muted-foreground">
            {[interview.candidate_label, interview.role].filter(Boolean).join(" · ")}
          </span>
          {interview.status === "FAILED" && interview.error && (
            <span className="line-clamp-2 text-xs text-destructive">{interview.error}</span>
          )}
        </div>
      </TableCell>
      <TableCell>
        <div className="flex flex-col gap-1">
          <InterviewStatusBadge status={interview.status} />
          {interview.status === "PREPARING" && interview.progress && (
            <span className="text-xs text-muted-foreground">{interview.progress}</span>
          )}
        </div>
      </TableCell>
      <TableCell>
        <Outcome interview={interview} />
      </TableCell>
      <TableCell className="text-xs whitespace-nowrap text-muted-foreground">
        {new Date(interview.created_at).toLocaleString()}
      </TableCell>
    </TableRow>
  );
}

function Interviews() {
  const interviews = useInterviews();
  useInterviewEvents();

  return (
    <section className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-3">
          <SectionNav current="/app/interviews" />
          <div className="flex flex-col gap-1">
            <h1 className="text-2xl font-semibold tracking-tight">Interviews</h1>
            <p className="max-w-2xl text-sm text-muted-foreground">
              Live text interviews run by the AI interviewer. Each one is planned from a role pack and, optionally, the
              candidate&apos;s CV and the job description, then graded against the rubric. Scores are advisory: a person
              makes every decision.
            </p>
          </div>
        </div>
        <NewInterviewDialog />
      </div>

      {interviews.isError && <p className="text-sm text-destructive">{interviews.error.message}</p>}
      {interviews.isPending && <p className="text-sm text-muted-foreground">Loading interviews…</p>}
      {interviews.data &&
        (interviews.data.length === 0 ? (
          <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
            No interviews yet. Use New interview to plan one.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Interview</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Outcome</TableHead>
                <TableHead>Created</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {interviews.data.map((interview) => (
                <InterviewRow key={interview.id} interview={interview} />
              ))}
            </TableBody>
          </Table>
        ))}
    </section>
  );
}

export function InterviewsConsole() {
  return <RequireAuth>{() => <Interviews />}</RequireAuth>;
}
