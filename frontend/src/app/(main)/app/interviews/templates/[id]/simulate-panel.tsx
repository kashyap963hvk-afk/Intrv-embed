"use client";

import { useState } from "react";

import { ListField } from "@/components/list-field";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { actionLabel, answerQualityLabel } from "@/lib/interviews";
import { useSimulate } from "@/lib/interviews-api";

const MAX_ANSWERS = 5;

/** A few interviewer turns against scripted candidate answers, to try the persona before using it. */
export function SimulatePanel({
  templateId,
  hasPack,
  dirty,
}: {
  templateId: string;
  hasPack: boolean;
  dirty: boolean;
}) {
  const [answers, setAnswers] = useState<string[]>([]);
  const simulate = useSimulate(templateId);
  const result = simulate.data;

  return (
    <Card>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <h2 className="font-semibold">Simulate</h2>
          <p className="text-sm text-muted-foreground">
            Runs the saved template against scripted answers (a strong, a vague and an &quot;I don&apos;t know&quot;
            answer unless you write your own) and shows how the interviewer reacts.
            {dirty && " Save your changes first to simulate them."}
          </p>
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="sim-answers">Your scripted answers (optional, one per line, up to {MAX_ANSWERS})</Label>
          <ListField id="sim-answers" value={answers} onChange={(next) => setAnswers(next.slice(0, MAX_ANSWERS))} />
        </div>
        <Button
          className="w-fit"
          variant="outline"
          disabled={!hasPack || simulate.isPending}
          title={hasPack ? undefined : "Choose a role pack and save first"}
          onClick={() => simulate.mutate(answers)}
        >
          {simulate.isPending ? "Simulating… (this calls the model a few times)" : "Run simulation"}
        </Button>
        {!hasPack && <p className="text-xs text-muted-foreground">Choose a role pack and save to simulate.</p>}
        {result && (
          <ol className="flex flex-col gap-3">
            <li className="flex flex-col gap-1">
              <span className="text-xs text-muted-foreground">Opening question</span>
              <p className="rounded-2xl rounded-bl-sm bg-muted px-4 py-2.5 text-sm">{result.opening_question}</p>
            </li>
            {result.turns.map((turn, i) => (
              <li key={i} className="flex flex-col gap-2">
                <div className="flex flex-col items-end gap-1">
                  <span className="text-xs text-muted-foreground">Scripted candidate</span>
                  <p className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-4 py-2.5 text-sm text-primary-foreground">
                    {turn.candidate}
                  </p>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  <Badge variant="outline">Answer: {answerQualityLabel(turn.answer_quality)}</Badge>
                  <Badge variant="secondary">Next: {actionLabel(turn.action)}</Badge>
                </div>
                <p className="max-w-[85%] rounded-2xl rounded-bl-sm bg-muted px-4 py-2.5 text-sm">{turn.interviewer}</p>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
