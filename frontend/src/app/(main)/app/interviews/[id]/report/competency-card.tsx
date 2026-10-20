import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { formatPercent, formatScore, turnAnchor, type CompetencyResult } from "@/lib/interviews";

/** One competency: score, anchor level and text, evidence quotes linked to the transcript, misconceptions. */
export function CompetencyCard({ competency }: { competency: CompetencyResult }) {
  return (
    <Card className="gap-3 py-4">
      <CardContent className="flex flex-col gap-3 px-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="flex min-w-0 flex-col gap-0.5">
            <h3 className="font-medium">{competency.name || competency.id}</h3>
            <span className="text-xs text-muted-foreground">Weight {formatPercent(competency.weight)}</span>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            {competency.needs_human_review && (
              <Badge
                variant="destructive"
                title={`Grading runs disagreed by ${competency.spread} points: read the evidence before relying on this score`}
              >
                Needs human review
              </Badge>
            )}
            {competency.assessed ? (
              <Badge variant="secondary" className="tabular-nums">
                {formatScore(competency.score)}
                {competency.anchor_level !== null && ` · level ${competency.anchor_level}`}
              </Badge>
            ) : (
              <Badge variant="outline">Not assessed</Badge>
            )}
          </div>
        </div>
        {competency.anchor && <p className="text-sm text-muted-foreground">{competency.anchor}</p>}
        {competency.evidence.length > 0 && (
          <div className="flex flex-col gap-1.5">
            <h4 className="text-xs font-medium text-muted-foreground uppercase">Evidence</h4>
            <ul className="flex flex-col gap-1.5">
              {competency.evidence.map((item, i) => (
                <li key={i}>
                  <a
                    href={`#${turnAnchor(item.turn_seq)}`}
                    className="block rounded-md border-l-2 bg-muted/50 px-3 py-1.5 text-sm hover:bg-muted"
                  >
                    “{item.quote}”
                    <span className="ml-1 text-xs whitespace-nowrap text-muted-foreground">turn {item.turn_seq}</span>
                  </a>
                </li>
              ))}
            </ul>
          </div>
        )}
        {competency.misconceptions.length > 0 && (
          <div className="flex flex-col gap-1">
            <h4 className="text-xs font-medium text-muted-foreground uppercase">Misconceptions</h4>
            <ul className="list-disc space-y-0.5 pl-5 text-sm">
              {competency.misconceptions.map((m, i) => (
                <li key={i}>{m}</li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
