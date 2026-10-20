import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  formatDurationMs,
  formatSeconds,
  signalCounts,
  signalDetail,
  signalLabel,
  turnAnchor,
  type IntegritySummary,
} from "@/lib/interviews";

const SEVERITY_VARIANT = { info: "outline", low: "secondary", medium: "default", high: "destructive" } as const;

/** Integrity signals for the reviewer: always labelled as signals, never as findings. */
export function IntegrityPanel({ integrity }: { integrity: IntegritySummary }) {
  const counts = signalCounts(integrity.counts);
  return (
    <Card>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <h2 className="flex flex-wrap items-center gap-2 font-semibold">
            Integrity signals
            <Badge variant={integrity.rollup === "worth a look" ? "default" : "secondary"}>{integrity.rollup}</Badge>
          </h2>
          <p className="text-sm font-medium">{integrity.label}</p>
        </div>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
          <dt className="text-muted-foreground">Time away</dt>
          <dd className="tabular-nums">{formatSeconds(integrity.time_away_seconds)}</dd>
          <dt className="text-muted-foreground">Medium or higher</dt>
          <dd className="tabular-nums">{integrity.medium_or_higher}</dd>
        </dl>
        {counts.length > 0 ? (
          <ul className="flex flex-wrap gap-1.5">
            {counts.map((c) => (
              <li key={c.kind}>
                <Badge variant="outline">
                  {c.label}: {c.n}
                </Badge>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No signals were recorded.</p>
        )}
        {integrity.events.length > 0 && (
          <details>
            <summary className="cursor-pointer text-sm font-medium">Timeline ({integrity.events.length})</summary>
            <ol className="mt-2 flex max-h-96 flex-col gap-2 overflow-y-auto text-sm">
              {integrity.events.map((event, i) => {
                const detail = signalDetail(event.detail);
                return (
                  <li key={i} className="flex flex-col gap-0.5 border-l-2 pl-3">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="text-xs text-muted-foreground tabular-nums">
                        {event.at ? new Date(event.at).toLocaleTimeString() : "—"}
                      </span>
                      <span className="font-medium">{signalLabel(event.kind)}</span>
                      <Badge variant={SEVERITY_VARIANT[event.severity]}>{event.severity}</Badge>
                    </div>
                    <span className="text-xs text-muted-foreground">
                      {[
                        event.source.toLowerCase(),
                        event.duration_ms !== null ? formatDurationMs(event.duration_ms) : null,
                        detail || null,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                      {event.turn_seq !== null && (
                        <>
                          {" · "}
                          <a href={`#${turnAnchor(event.turn_seq)}`} className="underline">
                            turn {event.turn_seq}
                          </a>
                        </>
                      )}
                    </span>
                  </li>
                );
              })}
            </ol>
          </details>
        )}
      </CardContent>
    </Card>
  );
}
