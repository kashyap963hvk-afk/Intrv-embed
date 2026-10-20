"use client";

import { ArrowLeft, FileText, LoaderCircle, Play, RotateCw, Square, Trash } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";

import { RequireAuth } from "@/components/auth/require-auth";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  canDelete,
  canEditPlan,
  canEnd,
  canInvite,
  canOpenRoom,
  canPrepareAgain,
  CAMERA_MODE_LABEL,
  hasReport,
  INTEGRITY_MODE_LABEL,
  parseLiveState,
  parsePlan,
  parseSettings,
  sectionLabel,
  STRICTNESS_LABEL,
  type InterviewDetail,
} from "@/lib/interviews";
import {
  useDeleteInterview,
  useEndInterview,
  useInterview,
  useInterviewEvents,
  usePrepareAgain,
} from "@/lib/interviews-api";

import { InterviewStatusBadge } from "../interview-status-badge";
import { InvitePanel } from "./invite-panel";
import { PlanEditor } from "./plan-editor";

function Header({ interview }: { interview: InterviewDetail }) {
  const prepare = usePrepareAgain(interview.id);
  const end = useEndInterview(interview.id);
  const remove = useDeleteInterview();
  const router = useRouter();
  const live = interview.status === "LIVE";
  const persona = interview.persona;
  const interviewer = typeof persona.interviewer_name === "string" ? persona.interviewer_name : null;

  return (
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div className="flex min-w-0 flex-col gap-1">
        <Link href="/app/interviews" className="flex w-fit items-center gap-1 text-sm text-muted-foreground">
          <ArrowLeft className="size-4" aria-hidden /> Interviews
        </Link>
        <h1 className="flex flex-wrap items-center gap-2 text-2xl font-semibold tracking-tight">
          {interview.title}
          <InterviewStatusBadge status={interview.status} />
        </h1>
        <p className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          {interview.candidate_label && <span>{interview.candidate_label}</span>}
          {interview.role && <span>· {interview.role}</span>}
          {interviewer && <span>· interviewer {interviewer}</span>}
          <span>· {new Date(interview.created_at).toLocaleString()}</span>
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        {hasReport(interview) && (
          <Link href={`/app/interviews/${interview.id}/report`} className={buttonVariants({ size: "sm" })}>
            <FileText /> Report
          </Link>
        )}
        {canOpenRoom(interview.status) && (
          <Link
            href={`/app/interviews/${interview.id}/room`}
            className={buttonVariants({ size: "sm", variant: "outline" })}
            title="Open the interview room as its creator"
          >
            <Play /> {live ? "Open the room" : "Take it yourself"}
          </Link>
        )}
        {canPrepareAgain(interview.status) && (
          <Button
            size="sm"
            variant="outline"
            disabled={prepare.isPending}
            onClick={() =>
              (interview.status === "FAILED" ||
                window.confirm("Plan this interview again? Your edits to the current plan are replaced.")) &&
              prepare.mutate(undefined)
            }
          >
            <RotateCw /> Prepare again
          </Button>
        )}
        {canEnd(interview.status) && (
          <Button
            size="sm"
            variant="outline"
            disabled={end.isPending}
            onClick={() =>
              window.confirm(
                live
                  ? "End the interview now? The conversation stops and what was said is graded."
                  : "Cancel this interview? The candidate link stops working.",
              ) && end.mutate(undefined)
            }
          >
            <Square /> {live ? "End interview" : "Cancel interview"}
          </Button>
        )}
        {canDelete(interview.status) && (
          <Button
            size="sm"
            variant="outline"
            className="text-destructive"
            disabled={remove.isPending}
            onClick={() =>
              window.confirm(`Delete ${interview.title}, its transcript and its report?`) &&
              remove.mutate(interview, { onSuccess: () => router.push("/app/interviews") })
            }
          >
            <Trash /> Delete
          </Button>
        )}
      </div>
    </div>
  );
}

function LiveCard({ interview }: { interview: InterviewDetail }) {
  const live = parseLiveState(interview.live);
  return (
    <Card>
      <CardContent className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
        <span className="flex items-center gap-2 font-medium">
          <span className="size-2 animate-pulse rounded-full bg-success" aria-hidden /> The interview is live
        </span>
        <span className="text-muted-foreground">
          {live.elapsed_minutes} of {live.total_minutes} min
        </span>
        {live.topic_count > 0 && (
          <span className="text-muted-foreground">
            Topic {live.topic_number} of {live.topic_count}
            {live.section && ` · ${sectionLabel(live.section)}`}
          </span>
        )}
      </CardContent>
    </Card>
  );
}

function SettingsSummary({ interview }: { interview: InterviewDetail }) {
  const s = parseSettings(interview.settings);
  const rows = [
    ["Duration", `${s.duration_minutes} min`],
    ["Follow-up depth", String(s.follow_up_depth)],
    ["Strictness", STRICTNESS_LABEL[s.strictness]],
    ["Hints", s.hints_allowed ? "Allowed" : "Off"],
    ["Integrity monitoring", INTEGRITY_MODE_LABEL[s.integrity_mode]],
    ["Camera check", CAMERA_MODE_LABEL[s.camera_monitoring]],
    ["Grading runs", String(s.grading_runs)],
    ["Language", s.language],
  ] as const;
  return (
    <Card>
      <CardContent className="flex flex-col gap-3">
        <h2 className="font-semibold">Settings</h2>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
          {rows.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-muted-foreground">{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}

function Context({ interview }: { interview: InterviewDetail }) {
  const plan = parsePlan(interview.plan);
  if (!plan.candidate.summary && plan.context_documents.length === 0) return null;
  return (
    <Card>
      <CardContent className="flex flex-col gap-3">
        {plan.candidate.summary && (
          <div className="flex flex-col gap-1">
            <h2 className="font-semibold">Candidate summary (blind)</h2>
            <p className="text-sm whitespace-pre-wrap text-muted-foreground">{plan.candidate.summary}</p>
          </div>
        )}
        {plan.context_documents.length > 0 && (
          <div className="flex flex-col gap-1">
            <h2 className="font-semibold">Reference material</h2>
            <div className="flex flex-wrap gap-1.5">
              {plan.context_documents.map((title) => (
                <Badge key={title} variant="outline">
                  {title}
                </Badge>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Workspace() {
  const { id } = useParams<{ id: string }>();
  const interview = useInterview(id);
  const prepare = usePrepareAgain(id);
  useInterviewEvents();

  if (interview.isPending) return <p className="text-sm text-muted-foreground">Loading interview…</p>;
  if (interview.isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Interview unavailable</AlertTitle>
        <AlertDescription>
          <p>{interview.error.message}</p>
          <Link href="/app/interviews" className={buttonVariants({ variant: "outline", size: "sm" })}>
            Back to interviews
          </Link>
        </AlertDescription>
      </Alert>
    );
  }
  const detail = interview.data;
  const plan = parsePlan(detail.plan);

  return (
    <section className="flex flex-col gap-6">
      <Header interview={detail} />
      {detail.status === "PREPARING" && (
        <Card>
          <CardContent className="flex flex-col gap-2">
            <p className="flex items-center gap-2 font-medium" role="status" aria-live="polite">
              <LoaderCircle className="size-4 animate-spin" aria-hidden />
              {detail.progress || "Working…"}
            </p>
            <p className="text-sm text-muted-foreground">
              Reading the CV, comparing it with the job description, planning topics and gathering reference material.
              You can leave this page; preparation continues and this page updates live.
            </p>
          </CardContent>
        </Card>
      )}
      {detail.status === "FAILED" && (
        <Alert variant="destructive">
          <AlertTitle>Preparation failed</AlertTitle>
          <AlertDescription>
            <p>{detail.error ?? "The interview could not be prepared."}</p>
            <Button size="sm" variant="outline" disabled={prepare.isPending} onClick={() => prepare.mutate(undefined)}>
              <RotateCw /> {prepare.isPending ? "Retrying…" : "Prepare again"}
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {detail.status === "LIVE" && <LiveCard interview={detail} />}
      {detail.status === "COMPLETED" && (
        <Alert>
          <AlertTitle>The interview is finished</AlertTitle>
          <AlertDescription>
            <p>The report is graded in the background and needs a human decision.</p>
            <Link href={`/app/interviews/${detail.id}/report`} className={buttonVariants({ size: "sm" })}>
              <FileText /> Open the report
            </Link>
          </AlertDescription>
        </Alert>
      )}
      <div className="grid gap-6 lg:grid-cols-[1fr_20rem]">
        <div className="flex min-w-0 flex-col gap-6">
          {canInvite(detail.status) && <InvitePanel interview={detail} />}
          {plan.topics.length > 0 && (
            <PlanEditor interviewId={detail.id} plan={plan} editable={canEditPlan(detail.status)} />
          )}
        </div>
        <div className="flex flex-col gap-6">
          <SettingsSummary interview={detail} />
          <Context interview={detail} />
        </div>
      </div>
    </section>
  );
}

export function InterviewPage() {
  return <RequireAuth>{() => <Workspace />}</RequireAuth>;
}
