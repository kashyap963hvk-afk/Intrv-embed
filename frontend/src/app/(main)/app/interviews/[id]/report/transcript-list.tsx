import { sectionLabel, turnAnchor, withSectionHeadings, type TranscriptTurn } from "@/lib/interviews";
import { cn } from "@/lib/utils";

const SPEAKER = { INTERVIEWER: "Interviewer", CANDIDATE: "Candidate", SYSTEM: "System" } as const;

/** The full transcript; each turn is an anchor target for evidence and integrity links. */
export function TranscriptList({ turns }: { turns: TranscriptTurn[] }) {
  if (turns.length === 0) return <p className="text-sm text-muted-foreground">No turns yet.</p>;
  return (
    <ol className="flex flex-col gap-3">
      {withSectionHeadings(turns).map(({ turn, heading }) => {
        return (
          <li key={turn.seq} className="flex flex-col gap-2">
            {heading && (
              <h3 className="mt-2 text-xs font-semibold text-muted-foreground uppercase">{sectionLabel(heading)}</h3>
            )}
            <div
              id={turnAnchor(turn.seq)}
              className={cn(
                "scroll-mt-24 rounded-lg border p-3 text-sm target:ring-2 target:ring-ring",
                turn.speaker === "CANDIDATE" && "bg-muted/50",
                turn.speaker === "SYSTEM" && "border-dashed text-muted-foreground",
              )}
            >
              <div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
                <span className="font-medium text-foreground">{SPEAKER[turn.speaker]}</span>
                <span className="tabular-nums">#{turn.seq}</span>
                {turn.at && <span className="tabular-nums">{new Date(turn.at).toLocaleTimeString()}</span>}
              </div>
              <p className="leading-relaxed whitespace-pre-wrap">{turn.text}</p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
