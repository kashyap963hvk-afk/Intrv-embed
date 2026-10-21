"use client";

import { ArrowLeft, Download, LoaderCircle, RotateCw, Scale } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { RequireAuth } from "@/components/auth/require-auth";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  ADVISORY_LABEL,
  formatPercent,
  formatScore,
  recommendationLabel,
  recommendationTone,
  REPORT_FORMATS,
  type Narrative,
  type Report,
  type Scorecard,
} from "@/lib/interviews";
import { useExportReport, useInterviewEvents, useRegrade, useReport } from "@/lib/interviews-api";
import { useReportExtras } from "@/lib/report-extras-api";

import { InterviewStatusBadge } from "../../interview-status-badge";
import { ToneBadge } from "../../tone-badge";
import { CompetencyCard } from "./competency-card";
import { DecisionForm } from "./decision-form";
import { IntegrityPanel } from "./integrity-panel";
import { PracticalWork, SnapshotStrip } from "./practical-work";
import { TranscriptList } from "./transcript-list";

function Bullets({ title, items, empty }: { title: string; items: string[]; empty?: string }) {
  if (items.length === 0 && !empty) return null;
  return (
    <div className="flex flex-col gap-1">
      <h3 className="text-sm font-semibold">{title}</h3>
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{empty}</p>
      ) : (
        <ul className="list-disc space-y-0.5 pl-5 text-sm">
          {items.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Header({ id, report }: { id: string; report: Report }) {
  const exportReport = useExportReport();
  return (
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div className="flex min-w-0 flex-col gap-1">
        <Link href={`/app/interviews/${id}`} className="flex w-fit items-center gap-1 text-sm text-muted-foreground">
          <ArrowLeft className="size-4" aria-hidden /> Interview
        </Link>
        <h1 className="flex flex-wrap items-center gap-2 text-2xl font-semibold tracking-tight">
          {report.title}
          <InterviewStatusBadge status={report.status} />
        </h1>
        <p className="flex flex-wrap gap-2 text-sm text-muted-foreground">
          {report.candidate_label && <span>{report.candidate_label}</span>}
          {report.role && <span>· {report.role}</span>}
          {report.started_at && <span>· {new Date(report.started_at).toLocaleString()}</span>}
          {report.ended_at && <span>to {new Date(report.ended_at).toLocaleTimeString()}</span>}
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        {REPORT_FORMATS.map(({ format, label }) => (
          <Button
            key={format}
            size="sm"
            variant="outline"
            disabled={exportReport.isPending}
            onClick={() => exportReport.mutate({ id, title: report.title, format })}
          >
            <Download /> {label}
          </Button>
        ))}
      </div>
    </div>
  );
}

function Summary({ scorecard }: { scorecard: Scorecard }) {
  const data = scorecard.data;
  if (!data) return null;
  const narrative = data.narrative;
  const recommendation = narrative?.recommendation ?? scorecard.recommendation;
  return (
    <Card>
      <CardContent className="flex flex-col gap-4">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="flex flex-col gap-1">
            <span className="text-xs text-muted-foreground uppercase">AI recommendation</span>
            <ToneBadge tone={recommendationTone(recommendation)}>{recommendationLabel(recommendation)}</ToneBadge>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs text-muted-foreground uppercase">From the scores alone</span>
            <ToneBadge tone={recommendationTone(data.score_based_recommendation)}>
              {recommendationLabel(data.score_based_recommendation)}
            </ToneBadge>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs text-muted-foreground uppercase">Overall score</span>
            <span className="text-lg font-semibold tabular-nums">{formatScore(data.overall_score)}</span>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs text-muted-foreground uppercase">Rubric weight covered</span>
            <span className="text-lg font-semibold tabular-nums">{formatPercent(data.weight_covered)}</span>
          </div>
        </div>
        {narrative?.recommendation_reason && (
          <p className="text-sm text-muted-foreground">{narrative.recommendation_reason}</p>
        )}
        {data.variance_flags.length > 0 && (
          <p className="text-sm">
            Grading runs disagreed on {data.variance_flags.length}{" "}
            {data.variance_flags.length === 1 ? "competency" : "competencies"}; those are marked for human review.
          </p>
        )}
        {data.narrative_source === "fallback" && (
          <p className="text-sm text-muted-foreground">
            No model was available to write the narrative: it was derived from the scores.
          </p>
        )}
        {data.grading_problems.length > 0 && (
          <Bullets title="Grading problems (some topics may be missing)" items={data.grading_problems} />
        )}
      </CardContent>
    </Card>
  );
}

function NarrativeSections({ narrative }: { narrative: Narrative }) {
  return (
    <Card>
      <CardContent className="flex flex-col gap-4">
        {narrative.summary && (
          <div className="flex flex-col gap-1">
            <h2 className="font-semibold">Summary</h2>
            <p className="text-sm leading-relaxed whitespace-pre-wrap">{narrative.summary}</p>
          </div>
        )}
        <div className="grid gap-4 sm:grid-cols-2">
          <Bullets title="Strengths" items={narrative.strengths} empty="None noted." />
          <Bullets title="Concerns" items={narrative.concerns} empty="None noted." />
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          <Bullets title="CV claims verified" items={narrative.claims_verified} empty="None." />
          <Bullets title="CV claims contradicted" items={narrative.claims_contradicted} empty="None." />
          <Bullets title="CV claims not tested" items={narrative.claims_untested} empty="None." />
        </div>
        {narrative.knowledge_gaps.length > 0 && (
          <div className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold">Knowledge gaps</h3>
            <ul className="flex flex-col gap-2">
              {narrative.knowledge_gaps.map((gap, i) => (
                <li key={i} className="rounded-md border p-3 text-sm">
                  <p className="font-medium">{gap.topic}</p>
                  <p className="text-muted-foreground">{gap.explanation}</p>
                  {gap.citations.length > 0 && (
                    <p className="mt-1 text-xs text-muted-foreground">Sources: {gap.citations.join(", ")}</p>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
        <Bullets title="Focus for the next round" items={narrative.next_round_focus} />
      </CardContent>
    </Card>
  );
}

function Grading({ id, report }: { id: string; report: Report }) {
  const regrade = useRegrade(id);
  const scorecard = report.scorecard;

  if (report.status === "LIVE") {
    return (
      <Alert>
        <AlertTitle>The interview is still in progress</AlertTitle>
        <AlertDescription>The transcript below updates as it goes; grading starts when it ends.</AlertDescription>
      </Alert>
    );
  }
  if (!scorecard || scorecard.status === "PENDING") {
    if (!scorecard && report.status !== "COMPLETED") {
      return (
        <Alert>
          <AlertTitle>No report</AlertTitle>
          <AlertDescription>This interview did not take place, so there is nothing to grade.</AlertDescription>
        </Alert>
      );
    }
    return (
      <Card>
        <CardContent className="flex flex-col gap-2">
          <p className="flex items-center gap-2 font-medium" role="status" aria-live="polite">
            <LoaderCircle className="size-4 animate-spin" aria-hidden />
            Grading the interview…
          </p>
          <p className="text-sm text-muted-foreground">
            Each topic is graded several times against the rubric and the median kept. This page updates when the report
            is ready.
          </p>
        </CardContent>
      </Card>
    );
  }
  if (scorecard.status === "FAILED") {
    return (
      <Alert variant="destructive">
        <AlertTitle>Grading failed</AlertTitle>
        <AlertDescription>
          <p>{scorecard.error ?? "The interview could not be graded."}</p>
          <Button size="sm" variant="outline" disabled={regrade.isPending} onClick={() => regrade.mutate()}>
            <RotateCw /> {regrade.isPending ? "Starting…" : "Grade again"}
          </Button>
        </AlertDescription>
      </Alert>
    );
  }
  const data = scorecard.data;
  return (
    <>
      <Summary scorecard={scorecard} />
      {data && data.competencies.length > 0 && (
        <section className="flex flex-col gap-3">
          <h2 className="text-lg font-semibold">Competencies</h2>
          <div className="grid gap-3 md:grid-cols-2">
            {data.competencies.map((c) => (
              <CompetencyCard key={c.id} competency={c} />
            ))}
          </div>
        </section>
      )}
      {data?.narrative && <NarrativeSections narrative={data.narrative} />}
    </>
  );
}

function ReportView() {
  const { id } = useParams<{ id: string }>();
  const report = useReport(id);
  const extras = useReportExtras(id, report.data?.status === "LIVE");
  useInterviewEvents();

  if (report.isPending) return <p className="text-sm text-muted-foreground">Loading report…</p>;
  if (report.isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Report unavailable</AlertTitle>
        <AlertDescription>
          <p>{report.error.message}</p>
          <Link href="/app/interviews" className={buttonVariants({ variant: "outline", size: "sm" })}>
            Back to interviews
          </Link>
        </AlertDescription>
      </Alert>
    );
  }
  const data = report.data;
  const scorecard = data.scorecard;

  return (
    <section className="flex flex-col gap-6">
      <Header id={id} report={data} />
      <div
        role="note"
        className="flex items-center gap-3 rounded-lg border-2 border-primary/70 bg-muted px-4 py-3 text-base font-semibold"
      >
        <Scale className="size-5 shrink-0" aria-hidden />
        {scorecard?.data?.label || ADVISORY_LABEL}
      </div>
      <div className="grid gap-6 lg:grid-cols-[1fr_22rem]">
        <div className="flex min-w-0 flex-col gap-6">
          <Grading id={id} report={data} />
          <PracticalWork submissions={extras.data?.practical ?? []} topics={data.plan.topics} />
        </div>
        <div className="flex flex-col gap-6">
          <DecisionForm key={scorecard?.reviewed_at ?? "none"} interviewId={id} scorecard={scorecard} />
          <IntegrityPanel integrity={data.integrity} />
          <SnapshotStrip snapshots={extras.data?.snapshots ?? []} />
        </div>
      </div>
      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold">Transcript</h2>
        <TranscriptList turns={data.transcript} />
      </section>
    </section>
  );
}

export function ReportPage() {
  return <RequireAuth>{() => <ReportView />}</RequireAuth>;
}
