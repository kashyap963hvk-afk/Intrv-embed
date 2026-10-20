import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { diffIsEmpty, formatWeight, weightChangeRows, type PackDiff } from "@/lib/rolepacks";

function IdList({
  title,
  ids,
  variant,
}: {
  title: string;
  ids: string[];
  variant: "success" | "destructive" | "secondary";
}) {
  if (ids.length === 0) return null;
  return (
    <div className="flex flex-col gap-1">
      <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{title}</p>
      <div className="flex flex-wrap gap-1">
        {ids.map((id) => (
          <Badge key={id} variant={variant} className="font-mono">
            {id}
          </Badge>
        ))}
      </div>
    </div>
  );
}

function weight(value: number | null): string {
  return value === null ? "—" : formatWeight(value);
}

/** What the draft changes compared with the base pack: competencies, role variants and weights. */
export function PackDiffView({ diff, baseName }: { diff: PackDiff; baseName: string }) {
  if (diffIsEmpty(diff)) {
    return <p className="text-sm text-muted-foreground">Same competencies, roles and weights as {baseName}.</p>;
  }
  const rows = weightChangeRows(diff);
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted-foreground">Compared with {baseName}:</p>
      <IdList title="Added competencies" ids={diff.added_competencies} variant="success" />
      <IdList title="Removed competencies" ids={diff.removed_competencies} variant="destructive" />
      <IdList title="Changed competencies" ids={diff.changed_competencies} variant="secondary" />
      <IdList title="Added roles" ids={diff.added_roles} variant="success" />
      <IdList title="Removed roles" ids={diff.removed_roles} variant="destructive" />
      {rows.length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Weight changes</p>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Role</TableHead>
                <TableHead>Competency</TableHead>
                <TableHead className="text-right">Before</TableHead>
                <TableHead className="text-right">After</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={`${row.role}:${row.competency}`}>
                  <TableCell className="font-mono text-xs">{row.role}</TableCell>
                  <TableCell className="font-mono text-xs">{row.competency}</TableCell>
                  <TableCell className="text-right tabular-nums">{weight(row.before)}</TableCell>
                  <TableCell className="text-right tabular-nums">{weight(row.after)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
