"use client";

import { ArrowDown, ArrowUp, Pencil, Trash } from "lucide-react";
import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  moveTopic,
  planSections,
  removeTopic,
  sectionLabel,
  TOPIC_LIMITS,
  topicEditProblem,
  updateTopic,
  type Plan,
  type PlanTopic,
} from "@/lib/interviews";
import { useUpdatePlan } from "@/lib/interviews-api";

const SOURCE_LABEL: Record<PlanTopic["source"]["type"], string> = {
  cv_claim: "CV claim",
  jd_gap: "JD gap",
  rubric: "Rubric",
  question_set: "Question set",
};

function List({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="flex flex-col gap-1">
      <h4 className="text-xs font-medium text-muted-foreground uppercase">{title}</h4>
      <ul className="list-disc space-y-0.5 pl-5 text-sm">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function TopicEditor({
  topic,
  saving,
  onSave,
  onCancel,
}: {
  topic: PlanTopic;
  saving: boolean;
  onSave: (patch: { title: string; seed_question: string }) => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState(topic.title);
  const [seed, setSeed] = useState(topic.seed_question);
  const problem = topicEditProblem(title, seed);
  const id = `topic-${topic.id}`;

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (!problem) onSave({ title: title.trim(), seed_question: seed.trim() });
      }}
    >
      <div className="flex flex-col gap-2">
        <Label htmlFor={`${id}-title`}>Title</Label>
        <Input
          id={`${id}-title`}
          value={title}
          maxLength={TOPIC_LIMITS.titleMax}
          autoFocus
          onChange={(e) => setTitle(e.target.value)}
        />
      </div>
      <div className="flex flex-col gap-2">
        <Label htmlFor={`${id}-seed`}>Seed question</Label>
        <Textarea
          id={`${id}-seed`}
          rows={3}
          value={seed}
          maxLength={TOPIC_LIMITS.seedMax}
          onChange={(e) => setSeed(e.target.value)}
        />
      </div>
      {problem && <p className="text-xs text-destructive">{problem}</p>}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={Boolean(problem) || saving}>
          {saving ? "Saving…" : "Save"}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function TopicCard({
  topic,
  number,
  editable,
  saving,
  canMoveUp,
  canMoveDown,
  canRemove,
  onMove,
  onRemove,
  onEdit,
}: {
  topic: PlanTopic;
  number: number;
  editable: boolean;
  saving: boolean;
  canMoveUp: boolean;
  canMoveDown: boolean;
  canRemove: boolean;
  onMove: (delta: -1 | 1) => void;
  onRemove: () => void;
  onEdit: (patch: { title: string; seed_question: string }, done: () => void) => void;
}) {
  const [editing, setEditing] = useState(false);

  return (
    <li>
      <Card className="gap-3 py-4">
        <CardContent className="flex flex-col gap-3 px-4">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="flex min-w-0 flex-col gap-1">
              <h3 className="font-medium">
                <span className="text-muted-foreground tabular-nums">{number}.</span> {topic.title}
              </h3>
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge variant="outline">{SOURCE_LABEL[topic.source.type]}</Badge>
                {topic.competencies.map((c) => (
                  <Badge key={c} variant="secondary">
                    {c}
                  </Badge>
                ))}
                <span className="text-xs text-muted-foreground">about {topic.planned_minutes} min</span>
              </div>
            </div>
            {editable && !editing && (
              <div className="flex items-center gap-1">
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label="Move up"
                  title="Move up"
                  disabled={!canMoveUp || saving}
                  onClick={() => onMove(-1)}
                >
                  <ArrowUp />
                </Button>
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label="Move down"
                  title="Move down"
                  disabled={!canMoveDown || saving}
                  onClick={() => onMove(1)}
                >
                  <ArrowDown />
                </Button>
                <Button size="icon" variant="ghost" aria-label="Edit" title="Edit" onClick={() => setEditing(true)}>
                  <Pencil />
                </Button>
                <Button
                  size="icon"
                  variant="ghost"
                  className="text-destructive"
                  aria-label="Remove topic"
                  title={canRemove ? "Remove topic" : "A plan needs at least one topic"}
                  disabled={!canRemove || saving}
                  onClick={() => window.confirm(`Remove the topic “${topic.title}”?`) && onRemove()}
                >
                  <Trash />
                </Button>
              </div>
            )}
          </div>
          {editing ? (
            <TopicEditor
              topic={topic}
              saving={saving}
              onSave={(patch) => onEdit(patch, () => setEditing(false))}
              onCancel={() => setEditing(false)}
            />
          ) : (
            <p className="rounded-md bg-muted px-3 py-2 text-sm">{topic.seed_question}</p>
          )}
          {topic.rationale && <p className="text-xs text-muted-foreground">{topic.rationale}</p>}
          <div className="grid gap-3 sm:grid-cols-2">
            <List title="Probe angles" items={topic.probe_angles} />
            <List title="Expected signals" items={topic.expected_signals} />
          </div>
          <List title="Red flags" items={topic.red_flags} />
        </CardContent>
      </Card>
    </li>
  );
}

/** The interview plan by section; editable (title, seed question, order, removal) until the interview starts. */
export function PlanEditor({ interviewId, plan, editable }: { interviewId: string; plan: Plan; editable: boolean }) {
  const save = useUpdatePlan(interviewId);
  const sections = planSections(plan);
  const topics = plan.topics;

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 className="text-lg font-semibold">
          Plan: {topics.length} {topics.length === 1 ? "topic" : "topics"}
        </h2>
        <p className="text-sm text-muted-foreground">
          {editable
            ? "Edit a topic's title or seed question, reorder topics within a section or remove them. Changes save immediately. The candidate never sees the plan."
            : "The plan is fixed once the interview starts. The candidate never sees it."}
        </p>
      </div>
      {plan.source === "fallback" && (
        <Alert>
          <AlertTitle>Built from the role pack</AlertTitle>
          <AlertDescription>
            No AI model answered while planning, so this plan was assembled from the role pack&apos;s questions (and the
            question set, if any). Review it, or use Prepare again once a model is available.
          </AlertDescription>
        </Alert>
      )}
      {sections.map((group) => (
        <div key={group.section} className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold text-muted-foreground">
            {sectionLabel(group.section)}
            {group.minutes !== null && ` · ${group.minutes} min`}
          </h3>
          <ol className="flex flex-col gap-3">
            {group.topics.map(({ topic, index }) => (
              <TopicCard
                key={topic.id || index}
                topic={topic}
                number={index + 1}
                editable={editable}
                saving={save.isPending}
                canMoveUp={moveTopic(topics, index, -1) !== null}
                canMoveDown={moveTopic(topics, index, 1) !== null}
                canRemove={removeTopic(topics, index) !== null}
                onMove={(delta) => {
                  const next = moveTopic(topics, index, delta);
                  if (next) save.mutate(next);
                }}
                onRemove={() => {
                  const next = removeTopic(topics, index);
                  if (next) save.mutate(next);
                }}
                onEdit={(patch, done) => save.mutate(updateTopic(topics, index, patch), { onSuccess: done })}
              />
            ))}
          </ol>
        </div>
      ))}
    </section>
  );
}
