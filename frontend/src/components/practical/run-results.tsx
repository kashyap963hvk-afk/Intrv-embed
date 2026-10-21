import { CheckCircle2, CircleAlert, XCircle } from "lucide-react";

import { runSummary, type RunReport } from "@/lib/practical";
import { cn } from "@/lib/utils";

function Lines({ title, lines, tone }: { title: string; lines: string[]; tone: "warning" | "error" }) {
  if (lines.length === 0) return null;
  return (
    <details open={tone === "error"} className="rounded-md border bg-muted/30 px-3 py-2">
      <summary className="cursor-pointer text-xs font-medium">
        {title} ({lines.length})
      </summary>
      <pre
        className={cn(
          "mt-2 max-h-48 overflow-auto font-mono text-xs leading-relaxed whitespace-pre-wrap",
          tone === "error" ? "text-destructive" : "text-amber-700 dark:text-amber-400",
        )}
      >
        {lines.join("\n")}
      </pre>
    </details>
  );
}

/** Compile status, compiler diagnostics and per-test results of a run (line numbers are the candidate's own). */
export function RunResults({ report }: { report: RunReport }) {
  return (
    <div className="flex flex-col gap-2 text-sm" role="status">
      <p className="flex items-center gap-1.5 font-medium">
        {report.compiled ? (
          <CheckCircle2 className="size-4 text-emerald-600" aria-hidden />
        ) : (
          <XCircle className="size-4 text-destructive" aria-hidden />
        )}
        {runSummary(report)}
      </p>
      <Lines title="Compiler errors" lines={report.errors} tone="error" />
      <Lines title="Compiler warnings" lines={report.warnings} tone="warning" />
      {report.tests.length > 0 && (
        <ol className="flex flex-col gap-1.5">
          {report.tests.map((test, index) => (
            <li key={index} className="rounded-md border px-3 py-2">
              <p className="flex items-center gap-1.5 text-xs font-medium">
                {test.passed ? (
                  <CheckCircle2 className="size-3.5 text-emerald-600" aria-hidden />
                ) : (
                  <CircleAlert className="size-3.5 text-destructive" aria-hidden />
                )}
                Test {index + 1}: {test.passed ? "passed" : "failed"}
                {test.detail && <span className="font-normal text-muted-foreground"> · {test.detail}</span>}
              </p>
              {!test.passed && (
                <dl className="mt-1.5 grid gap-1 font-mono text-xs sm:grid-cols-3">
                  {(
                    [
                      ["Input", test.stdin],
                      ["Expected", test.expected],
                      ["Your output", test.actual],
                    ] as const
                  ).map(([label, value]) => (
                    <div key={label} className="min-w-0">
                      <dt className="font-sans text-muted-foreground">{label}</dt>
                      <dd className="overflow-x-auto rounded bg-muted px-1.5 py-1 whitespace-pre">
                        {value || "(empty)"}
                      </dd>
                    </div>
                  ))}
                </dl>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
