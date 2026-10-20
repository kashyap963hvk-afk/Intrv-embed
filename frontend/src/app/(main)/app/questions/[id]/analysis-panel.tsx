import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  competencyName,
  planByCompetency,
  requirementLabel,
  resolveEvidence,
  type Analysis,
  type GapRequirement,
} from "@/lib/questions";

const REQUIREMENT_VARIANT = {
  met: "success",
  partial: "secondary",
  missing: "destructive",
  unclear: "outline",
} as const satisfies Record<GapRequirement["status"], string>;

function Count({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex flex-col rounded-md border px-3 py-2">
      <span className="text-lg font-semibold tabular-nums">{value}</span>
      <span className="text-xs text-muted-foreground">{label}</span>
    </div>
  );
}

/** How the set was made: the critic pass, the plan per competency, notes, and the JD gap matrix. */
export function AnalysisPanel({
  analysis,
  competencyNames,
  evidenceIndex,
}: {
  analysis: Analysis;
  competencyNames: Readonly<Record<string, string>>;
  evidenceIndex: Readonly<Record<string, string>> | null;
}) {
  const plan = planByCompetency(analysis.plan);
  const requirements = analysis.gap_matrix?.requirements ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle>How this set was made</CardTitle>
        <CardDescription>A critic reviewed every generated question; duplicates were removed.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 text-sm">
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
          <Count label="kept" value={analysis.critic.kept} />
          <Count label="rewritten" value={analysis.critic.rewritten} />
          <Count label="dropped" value={analysis.critic.dropped} />
          <Count label="duplicates" value={analysis.critic.duplicates} />
          <Count label="from the pack" value={analysis.critic.topped_up} />
        </div>
        {plan.length > 0 && (
          <div className="flex flex-col gap-1">
            <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Plan by competency</p>
            <div className="flex flex-wrap gap-1">
              {plan.map((row) => (
                <Badge key={row.competency} variant="outline">
                  {competencyName(row.competency, competencyNames)} × {row.count}
                </Badge>
              ))}
            </div>
          </div>
        )}
        {analysis.notes.length > 0 && (
          <div className="flex flex-col gap-1">
            <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Notes</p>
            <ul className="list-disc pl-5 text-muted-foreground">
              {analysis.notes.map((note, i) => (
                <li key={i}>{note}</li>
              ))}
            </ul>
          </div>
        )}
        {analysis.gap_matrix && (
          <div className="flex flex-col gap-1">
            <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Job description vs CV</p>
            {requirements.length === 0 ? (
              <p className="text-muted-foreground">No requirements were found in the job description.</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Requirement</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Competency</TableHead>
                    <TableHead>Evidence in the CV</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {requirements.map((req, i) => (
                    <TableRow key={i}>
                      <TableCell className="max-w-xs align-top whitespace-normal">
                        {req.text}
                        {req.note && <p className="text-xs text-muted-foreground">{req.note}</p>}
                      </TableCell>
                      <TableCell className="align-top">
                        <Badge variant={REQUIREMENT_VARIANT[req.status]}>{requirementLabel(req.status)}</Badge>
                      </TableCell>
                      <TableCell className="align-top text-xs">
                        {req.competency_id ? competencyName(req.competency_id, competencyNames) : "—"}
                      </TableCell>
                      <TableCell className="max-w-sm align-top text-xs whitespace-normal">
                        {req.evidence_refs.length === 0 ? (
                          <span className="text-muted-foreground">—</span>
                        ) : (
                          <ul className="flex flex-col gap-1">
                            {resolveEvidence(req.evidence_refs, evidenceIndex).map(({ ref, text }) => (
                              <li key={ref} title={ref}>
                                {text ?? <span className="font-mono">{ref}</span>}
                              </li>
                            ))}
                          </ul>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
