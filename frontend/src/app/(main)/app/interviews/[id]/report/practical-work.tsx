import { Camera, Check, X } from "lucide-react";
import Image from "next/image";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  fixLabel,
  hasCodeReview,
  isWhiteboard,
  practicalKindLabel,
  SNAPSHOT_LABEL,
  submissionTitle,
  testSummary,
  testTone,
  type CodeReview,
  type PracticalSubmission,
  type RunResults,
} from "@/lib/report-extras";

import { ToneBadge } from "../../tone-badge";

function Field({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <h4 className="text-xs font-semibold text-muted-foreground uppercase">{title}</h4>
      {children}
    </div>
  );
}

function Results({ results }: { results: RunResults }) {
  const failed = results.tests.filter((t) => !t.passed);
  return (
    <>
      {results.errors.length > 0 && (
        <Field title="Compiler errors">
          <pre className="max-h-48 overflow-auto rounded-md bg-muted p-2 font-mono text-xs whitespace-pre-wrap">
            {results.errors.join("\n")}
          </pre>
        </Field>
      )}
      <Field title={`Compiler warnings (${results.warnings.length})`}>
        {results.warnings.length > 0 ? (
          <pre className="max-h-48 overflow-auto rounded-md bg-muted p-2 font-mono text-xs whitespace-pre-wrap">
            {results.warnings.join("\n")}
          </pre>
        ) : (
          <p className="text-sm text-muted-foreground">None.</p>
        )}
      </Field>
      {failed.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer font-medium">Failed tests ({failed.length})</summary>
          <ul className="mt-2 flex flex-col gap-2">
            {failed.map((test, i) => (
              <li key={i} className="grid gap-1 rounded-md border p-2 font-mono text-xs sm:grid-cols-3">
                <span className="break-all whitespace-pre-wrap">
                  <span className="font-sans text-muted-foreground">input </span>
                  {test.stdin || "—"}
                </span>
                <span className="break-all whitespace-pre-wrap">
                  <span className="font-sans text-muted-foreground">expected </span>
                  {test.expected || "—"}
                </span>
                <span className="break-all whitespace-pre-wrap">
                  <span className="font-sans text-muted-foreground">got </span>
                  {test.actual || test.detail || "—"}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}

function Review({ review }: { review: CodeReview }) {
  return (
    <div className="flex flex-col gap-3 rounded-md border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-semibold">Reviewer&apos;s verified findings</h4>
        <ToneBadge
          tone={review.fix_correct === true ? "positive" : review.fix_correct === false ? "negative" : "neutral"}
        >
          {fixLabel(review.fix_correct)}
        </ToneBadge>
        {review.reasoning && <Badge variant="outline">reasoning: {review.reasoning}</Badge>}
      </div>
      {review.summary && <p className="text-sm">{review.summary}</p>}
      <Field title={`Known bugs found (${review.found.length})`}>
        {review.found.length > 0 ? (
          <ul className="flex flex-col gap-1.5 text-sm">
            {review.found.map((found) => (
              <li key={found.bug} className="flex gap-2">
                <Check className="mt-0.5 size-4 shrink-0 text-success" aria-hidden />
                <span>
                  Bug {found.bug}
                  {found.quote && (
                    <>
                      {": "}
                      <q className="text-muted-foreground italic">{found.quote}</q>
                    </>
                  )}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">None.</p>
        )}
      </Field>
      <Field title={`Known bugs missed (${review.missed.length})`}>
        {review.missed.length > 0 ? (
          <p className="flex items-center gap-2 text-sm">
            <X className="size-4 shrink-0 text-destructive" aria-hidden />
            {review.missed.map((n) => `Bug ${n}`).join(", ")}
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">None.</p>
        )}
      </Field>
      {review.new_bugs.length > 0 && (
        <Field title="New bugs introduced">
          <ul className="list-disc space-y-0.5 pl-5 text-sm">
            {review.new_bugs.map((bug, i) => (
              <li key={i}>{bug}</li>
            ))}
          </ul>
        </Field>
      )}
      <p className="text-xs text-muted-foreground">
        Each quote was checked against the candidate&apos;s code and explanation; a claimed find without a matching
        quote counts as missed.
      </p>
    </div>
  );
}

function SubmissionCard({ submission, title }: { submission: PracticalSubmission; title: string }) {
  const summary = testSummary(submission.results);
  const whiteboard = isWhiteboard(submission);
  return (
    <Card className="gap-3 py-4">
      <CardContent className="flex flex-col gap-4 px-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="flex min-w-0 flex-col gap-0.5">
            <h3 className="font-medium">{title}</h3>
            <span className="text-xs text-muted-foreground">
              {practicalKindLabel(submission.kind)} · task <span className="font-mono">{submission.task_id}</span>
              {submission.at && ` · ${new Date(submission.at).toLocaleTimeString()}`}
            </span>
          </div>
          {summary && <ToneBadge tone={testTone(submission.results)}>{summary}</ToneBadge>}
        </div>

        {whiteboard ? (
          <>
            {submission.image_url ? (
              <a href={submission.image_url} target="_blank" rel="noreferrer" className="w-fit">
                <Image
                  src={submission.image_url}
                  alt={`Candidate's diagram for ${title}`}
                  width={640}
                  height={400}
                  unoptimized
                  className="h-auto max-h-96 w-auto max-w-full rounded-md border bg-white object-contain"
                />
              </a>
            ) : (
              <p className="text-sm text-muted-foreground">The diagram image is no longer available.</p>
            )}
            <Field title="What the vision model saw">
              {submission.review?.description ? (
                <p className="text-sm whitespace-pre-wrap">{submission.review.description}</p>
              ) : (
                <p className="text-sm text-muted-foreground">
                  No vision model was available; the interviewer discussed the diagram with the candidate instead.
                </p>
              )}
            </Field>
          </>
        ) : (
          <>
            {submission.code && (
              <Field title="Code">
                <pre
                  className="max-h-96 overflow-auto rounded-md border bg-muted p-3 font-mono text-xs leading-relaxed"
                  tabIndex={0}
                  aria-label={`Submitted code for ${title}`}
                >
                  <code>{submission.code}</code>
                </pre>
              </Field>
            )}
            <Field title="Candidate's explanation">
              {submission.explanation ? (
                <p className="text-sm whitespace-pre-wrap">{submission.explanation}</p>
              ) : (
                <p className="text-sm text-muted-foreground">No written explanation.</p>
              )}
            </Field>
            {summary !== null && <Results results={submission.results} />}
            {hasCodeReview(submission.review) ? (
              <Review review={submission.review} />
            ) : (
              <p className="text-sm text-muted-foreground">
                No review: the reviewer model was unavailable when this was submitted. Read the code yourself.
              </p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

/** Practical submissions in the order they were made. */
export function PracticalWork({
  submissions,
  topics,
}: {
  submissions: readonly PracticalSubmission[];
  topics: readonly { id: string; title: string }[];
}) {
  if (submissions.length === 0) return null;
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-lg font-semibold">Practical work</h2>
      <div className="flex flex-col gap-3">
        {submissions.map((s, i) => (
          <SubmissionCard key={s.id || i} submission={s} title={submissionTitle(s, topics)} />
        ))}
      </div>
    </section>
  );
}

/** STRICT-mode camera snapshots, shown to the human reviewer only; never analysed or scored. */
export function SnapshotStrip({ snapshots }: { snapshots: readonly string[] }) {
  if (snapshots.length === 0) return null;
  return (
    <Card className="gap-3 py-4">
      <CardContent className="flex flex-col gap-3 px-4">
        <div className="flex flex-col gap-1">
          <h2 className="flex items-center gap-2 font-semibold">
            <Camera className="size-4" aria-hidden /> Camera snapshots ({snapshots.length})
          </h2>
          <p className="text-sm font-medium">{SNAPSHOT_LABEL}</p>
          <p className="text-xs text-muted-foreground">
            Taken with the candidate&apos;s consent in strict integrity mode. They are never analysed by the platform
            and never affect scores.
          </p>
        </div>
        <ul className="flex gap-2 overflow-x-auto pb-1">
          {snapshots.map((url, i) => (
            <li key={url} className="shrink-0">
              <a href={url} target="_blank" rel="noreferrer">
                <Image
                  src={url}
                  alt={`Snapshot ${i + 1} of ${snapshots.length}`}
                  width={160}
                  height={120}
                  unoptimized
                  className="h-24 w-auto rounded-md border object-cover"
                />
              </a>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
