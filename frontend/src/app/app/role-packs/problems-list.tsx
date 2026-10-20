import { CircleAlert, CircleCheck, LoaderCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { describePath, type PackSpec, type SpecProblem } from "@/lib/rolepacks";

/** "Valid" or the list of validation problems, each with the place in the pack it refers to. */
export function ProblemsList({
  problems,
  spec,
  checking = false,
}: {
  problems: readonly SpecProblem[];
  spec: PackSpec | null;
  checking?: boolean;
}) {
  if (problems.length === 0) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
        {checking ? (
          <>
            <LoaderCircle className="size-4 animate-spin" aria-hidden /> Checking…
          </>
        ) : (
          <>
            <CircleCheck className="size-4 text-success" aria-hidden /> Valid role pack
          </>
        )}
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-destructive/40 p-3" role="status">
      <p className="flex items-center gap-2 text-sm font-medium text-destructive">
        <CircleAlert className="size-4" aria-hidden />
        {problems.length === 1 ? "1 problem" : `${problems.length} problems`} to fix before saving
        {checking && <LoaderCircle className="size-4 animate-spin text-muted-foreground" aria-hidden />}
      </p>
      <ul className="flex max-h-56 flex-col gap-1 overflow-y-auto text-sm">
        {problems.map((problem, i) => (
          <li key={`${problem.path}-${i}`} className="flex flex-wrap items-baseline gap-2">
            <Badge variant="outline" className="font-mono" title={problem.path}>
              {describePath(problem.path, spec)}
            </Badge>
            <span>{problem.message}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
