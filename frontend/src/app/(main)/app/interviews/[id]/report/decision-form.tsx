"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { decisionLabel, DECISIONS, type Decision, type Scorecard } from "@/lib/interviews";
import { useRecordDecision } from "@/lib/interviews-api";
import { cn } from "@/lib/utils";

const HINT: Record<Decision, string> = {
  advance: "Move the candidate to the next round.",
  hold: "Undecided for now; revisit later.",
  reject: "Do not continue with this candidate.",
};

/** The human decision: the only outcome that counts. Mount with a key so a saved decision resets the form. */
export function DecisionForm({ interviewId, scorecard }: { interviewId: string; scorecard: Scorecard | null }) {
  const [decision, setDecision] = useState<Decision | null>(scorecard?.human_decision ?? null);
  const [notes, setNotes] = useState(scorecard?.decision_notes ?? "");
  const save = useRecordDecision(interviewId);
  const disabled = scorecard === null;

  return (
    <Card>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <h2 className="font-semibold">Human decision</h2>
          {scorecard?.human_decision ? (
            <p className="text-sm text-muted-foreground">
              Saved: <span className="font-medium text-foreground">{decisionLabel(scorecard.human_decision)}</span>
              {scorecard.reviewed_at && ` on ${new Date(scorecard.reviewed_at).toLocaleString()}`}
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">
              {disabled
                ? "Available once the interview has been graded."
                : "Read the evidence and the transcript, then decide."}
            </p>
          )}
        </div>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (decision) save.mutate({ decision, notes: notes.trim() });
          }}
        >
          <fieldset className="flex flex-col gap-2" disabled={disabled}>
            <legend className="sr-only">Decision</legend>
            {DECISIONS.map((value) => (
              <label
                key={value}
                className={cn(
                  "flex cursor-pointer items-start gap-3 rounded-md border p-2.5 text-sm has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-50",
                  decision === value && "border-primary bg-muted",
                )}
              >
                <input
                  type="radio"
                  name="decision"
                  value={value}
                  className="mt-0.5 accent-primary"
                  checked={decision === value}
                  onChange={() => setDecision(value)}
                />
                <span className="flex flex-col">
                  <span className="font-medium">{decisionLabel(value)}</span>
                  <span className="text-xs text-muted-foreground">{HINT[value]}</span>
                </span>
              </label>
            ))}
          </fieldset>
          <div className="flex flex-col gap-2">
            <Label htmlFor="decision-notes">Notes</Label>
            <Textarea
              id="decision-notes"
              rows={4}
              maxLength={4000}
              value={notes}
              disabled={disabled}
              placeholder="Why, in your own words (kept with the audit log)."
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>
          <Button type="submit" className="w-fit" disabled={disabled || !decision || save.isPending}>
            {save.isPending ? "Saving…" : "Save decision"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
