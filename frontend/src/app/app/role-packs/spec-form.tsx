"use client";

import { Plus, Scale, Trash } from "lucide-react";
import { useState, type ReactNode } from "react";

import { ListField } from "@/components/list-field";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import {
  addCompetency,
  addRole,
  ANCHOR_LEVELS,
  anchorText,
  emptySeedQuestion,
  formatWeight,
  normaliseWeights,
  removeCompetency,
  removeRole,
  SENIORITY,
  seniorityLabel,
  setAnchor,
  setWeight,
  updateCompetency,
  updateRole,
  weightsBalanced,
  weightTotal,
  type Competency,
  type PackSpec,
  type RoleVariant,
  type SeedQuestion,
} from "@/lib/rolepacks";
import { cn } from "@/lib/utils";

function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

/** A name input with an Add button (Enter adds too), for new competencies and role variants. */
function AddByName({ id, label, onAdd }: { id: string; label: string; onAdd: (name: string) => void }) {
  const [name, setName] = useState("");
  const add = () => {
    if (!name.trim()) return;
    onAdd(name.trim());
    setName("");
  };
  return (
    <div className="flex flex-wrap items-end gap-2">
      <div className="flex min-w-60 flex-1 flex-col gap-2">
        <Label htmlFor={id}>{label}</Label>
        <Input
          id={id}
          value={name}
          maxLength={120}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
        />
      </div>
      <Button type="button" variant="outline" disabled={!name.trim()} onClick={add}>
        <Plus /> Add
      </Button>
    </div>
  );
}

function SeedQuestionEditor({
  idPrefix,
  question,
  levels,
  onChange,
  onRemove,
}: {
  idPrefix: string;
  question: SeedQuestion;
  levels: readonly (typeof SENIORITY)[number][];
  onChange: (next: SeedQuestion) => void;
  onRemove: () => void;
}) {
  return (
    <div className="flex flex-col gap-3 rounded-md border p-3">
      <Field id={`${idPrefix}-text`} label="Question">
        <Textarea
          id={`${idPrefix}-text`}
          rows={2}
          maxLength={2000}
          value={question.text}
          onChange={(e) => onChange({ ...question, text: e.target.value })}
        />
      </Field>
      <div className="flex flex-wrap items-end gap-4">
        <Field id={`${idPrefix}-difficulty`} label="Difficulty">
          <NativeSelect
            id={`${idPrefix}-difficulty`}
            value={question.difficulty}
            onChange={(e) => onChange({ ...question, difficulty: Number(e.target.value) })}
          >
            {[1, 2, 3, 4, 5].map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-2 text-sm font-medium">Seniority</legend>
          <div className="flex flex-wrap gap-3">
            {levels.map((level) => (
              <label key={level} className="flex items-center gap-1.5 text-sm">
                <Checkbox
                  checked={question.level.includes(level)}
                  onCheckedChange={(checked) =>
                    onChange({
                      ...question,
                      level: checked ? [...question.level, level] : question.level.filter((l) => l !== level),
                    })
                  }
                />
                {seniorityLabel(level)}
              </label>
            ))}
          </div>
        </fieldset>
        <Button type="button" size="sm" variant="ghost" className="ml-auto text-destructive" onClick={onRemove}>
          <Trash /> Remove question
        </Button>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <Field id={`${idPrefix}-signals`} label="Expected signals (one per line)">
          <ListField
            id={`${idPrefix}-signals`}
            value={question.expected_signals}
            onChange={(expected_signals) => onChange({ ...question, expected_signals })}
          />
        </Field>
        <Field id={`${idPrefix}-follow-ups`} label="Follow-ups (one per line)">
          <ListField
            id={`${idPrefix}-follow-ups`}
            value={question.follow_ups}
            onChange={(follow_ups) => onChange({ ...question, follow_ups })}
          />
        </Field>
      </div>
    </div>
  );
}

function CompetencyEditor({
  competency,
  levels,
  onChange,
  onRemove,
}: {
  competency: Competency;
  levels: readonly (typeof SENIORITY)[number][];
  onChange: (next: Competency) => void;
  onRemove: () => void;
}) {
  const id = `comp-${competency.id}`;
  const missingAnchors = ANCHOR_LEVELS.filter((level) => !anchorText(competency, level).trim()).length;
  return (
    <details className="group rounded-lg border">
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 px-4 py-3 hover:bg-muted/50">
        <span className="font-medium">{competency.name || "Untitled competency"}</span>
        <span className="font-mono text-xs text-muted-foreground">{competency.id}</span>
        <span className="ml-auto flex flex-wrap gap-1">
          {missingAnchors > 0 && <Badge variant="destructive">{missingAnchors} anchors missing</Badge>}
          <Badge variant="outline">{competency.signals.length} signals</Badge>
          <Badge variant="outline">{competency.seed_questions.length} questions</Badge>
        </span>
      </summary>
      <div className="flex flex-col gap-4 border-t p-4">
        <div className="grid gap-4 md:grid-cols-[1fr_2fr]">
          <Field id={`${id}-name`} label="Name">
            <Input
              id={`${id}-name`}
              value={competency.name}
              maxLength={120}
              onChange={(e) => onChange({ ...competency, name: e.target.value })}
            />
          </Field>
          <Field id={`${id}-description`} label="Description">
            <Textarea
              id={`${id}-description`}
              rows={2}
              maxLength={2000}
              value={competency.description}
              onChange={(e) => onChange({ ...competency, description: e.target.value })}
            />
          </Field>
        </div>
        <fieldset className="flex flex-col gap-3">
          <legend className="mb-1 text-sm font-medium">Anchored levels</legend>
          <p className="text-xs text-muted-foreground">
            What an answer at each level sounds like, from 1 (weak) to 4 (expert). Graders score against these.
          </p>
          <div className="grid gap-3 md:grid-cols-2">
            {ANCHOR_LEVELS.map((level) => (
              <Field key={level} id={`${id}-anchor-${level}`} label={`Level ${level}`}>
                <Textarea
                  id={`${id}-anchor-${level}`}
                  rows={3}
                  maxLength={2000}
                  value={anchorText(competency, level)}
                  aria-invalid={!anchorText(competency, level).trim()}
                  onChange={(e) => onChange(setAnchor(competency, level, e.target.value))}
                />
              </Field>
            ))}
          </div>
        </fieldset>
        <div className="grid gap-4 md:grid-cols-2">
          <Field id={`${id}-signals`} label="Signals of a strong answer (one per line)">
            <ListField
              id={`${id}-signals`}
              value={competency.signals}
              rows={4}
              onChange={(signals) => onChange({ ...competency, signals })}
            />
          </Field>
          <Field id={`${id}-red-flags`} label="Red flags (one per line)">
            <ListField
              id={`${id}-red-flags`}
              value={competency.red_flags}
              rows={4}
              onChange={(red_flags) => onChange({ ...competency, red_flags })}
            />
          </Field>
        </div>
        <div className="flex flex-col gap-3">
          <p className="text-sm font-medium">Seed questions</p>
          <p className="text-xs text-muted-foreground">
            Fallbacks for question sets; generated questions grounded in the CV come first.
          </p>
          {competency.seed_questions.map((question, i) => (
            <SeedQuestionEditor
              key={i}
              idPrefix={`${id}-q${i}`}
              question={question}
              levels={levels}
              onChange={(next) =>
                onChange({
                  ...competency,
                  seed_questions: competency.seed_questions.map((q, j) => (j === i ? next : q)),
                })
              }
              onRemove={() =>
                onChange({ ...competency, seed_questions: competency.seed_questions.filter((_, j) => j !== i) })
              }
            />
          ))}
          <div className="flex flex-wrap justify-between gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() =>
                onChange({ ...competency, seed_questions: [...competency.seed_questions, emptySeedQuestion()] })
              }
            >
              <Plus /> Add seed question
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="text-destructive"
              onClick={() =>
                window.confirm(`Remove ${competency.name || competency.id} and its weight in every role?`) && onRemove()
              }
            >
              <Trash /> Remove competency
            </Button>
          </div>
        </div>
      </div>
    </details>
  );
}

function WeightsEditor({
  role,
  competencies,
  onChange,
}: {
  role: RoleVariant;
  competencies: readonly Competency[];
  onChange: (weights: Record<string, number>) => void;
}) {
  const total = weightTotal(role.weights);
  const balanced = weightsBalanced(role.weights);
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-col gap-1">
        {competencies.map((competency) => {
          const weight = role.weights[competency.id] ?? 0;
          const inputId = `weight-${role.id}-${competency.id}`;
          return (
            <div key={competency.id} className="grid items-center gap-2 sm:grid-cols-[minmax(0,14rem)_1fr_6rem]">
              <Label htmlFor={inputId} className="truncate font-normal" title={competency.name}>
                {competency.name || competency.id}
              </Label>
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={weight}
                aria-label={`${competency.name || competency.id} weight slider`}
                className="w-full accent-primary"
                onChange={(e) => onChange(setWeight(role.weights, competency.id, Number(e.target.value)))}
              />
              <Input
                id={inputId}
                type="number"
                min={0}
                max={1}
                step={0.01}
                className="text-right tabular-nums"
                value={weight}
                onChange={(e) => onChange(setWeight(role.weights, competency.id, Number(e.target.value)))}
              />
            </div>
          );
        })}
      </div>
      <div className="flex flex-wrap items-center justify-end gap-2 border-t pt-2">
        <span className={cn("text-sm tabular-nums", !balanced && "text-destructive")} role="status">
          Total {formatWeight(total)} {balanced ? "" : "(must be 1.00)"}
        </span>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={balanced || total <= 0}
          onClick={() => onChange(normaliseWeights(role.weights))}
        >
          <Scale /> Normalise
        </Button>
      </div>
    </div>
  );
}

/** The Form tab: basics, competencies and role variants. Everything else in the spec is kept unchanged. */
export function SpecForm({ spec, onChange }: { spec: PackSpec; onChange: (next: PackSpec) => void }) {
  return (
    <div className="flex flex-col gap-8">
      <section className="flex flex-col gap-4">
        <h2 className="text-lg font-semibold">Basics</h2>
        <Field id="pack-name" label="Name">
          <Input
            id="pack-name"
            value={spec.name}
            maxLength={120}
            onChange={(e) => onChange({ ...spec, name: e.target.value })}
          />
        </Field>
        <Field id="pack-description" label="Description">
          <Textarea
            id="pack-description"
            rows={3}
            maxLength={4000}
            value={spec.description}
            onChange={(e) => onChange({ ...spec, description: e.target.value })}
          />
        </Field>
        <Field id="pack-tags" label="Domain tags" hint="Comma-separated, e.g. firmware, rtos, automotive.">
          <ListField
            id="pack-tags"
            separator=","
            value={spec.domain_tags}
            onChange={(domain_tags) => onChange({ ...spec, domain_tags })}
          />
        </Field>
      </section>

      <section className="flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <h2 className="text-lg font-semibold">Competencies</h2>
          <p className="text-sm text-muted-foreground">
            {spec.competencies.length} {spec.competencies.length === 1 ? "competency" : "competencies"}. Open one to
            edit it.
          </p>
        </div>
        {spec.competencies.map((competency, i) => (
          <CompetencyEditor
            key={`${competency.id}-${i}`}
            competency={competency}
            levels={spec.seniority_levels}
            onChange={(next) => onChange(updateCompetency(spec, i, next))}
            onRemove={() => onChange(removeCompetency(spec, i))}
          />
        ))}
        <AddByName id="add-competency" label="New competency" onAdd={(name) => onChange(addCompetency(spec, name))} />
      </section>

      <section className="flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <h2 className="text-lg font-semibold">Role variants</h2>
          <p className="text-sm text-muted-foreground">
            How much each competency counts for a variant of the role. Weights must add up to 1.00; set a weight to 0 to
            leave a competency out.
          </p>
        </div>
        {spec.roles.map((role, i) => (
          <div key={`${role.id}-${i}`} className="flex flex-col gap-4 rounded-lg border p-4">
            <div className="flex flex-wrap items-end gap-3">
              <div className="flex min-w-60 flex-1 flex-col gap-2">
                <Label htmlFor={`role-${role.id}-name`}>
                  Variant name <span className="font-mono text-xs text-muted-foreground">{role.id}</span>
                </Label>
                <Input
                  id={`role-${role.id}-name`}
                  value={role.name}
                  maxLength={120}
                  onChange={(e) => onChange(updateRole(spec, i, { ...role, name: e.target.value }))}
                />
              </div>
              {spec.roles.length > 1 && (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="text-destructive"
                  onClick={() =>
                    window.confirm(`Remove the variant ${role.name || role.id}?`) && onChange(removeRole(spec, i))
                  }
                >
                  <Trash /> Remove variant
                </Button>
              )}
            </div>
            <WeightsEditor
              role={role}
              competencies={spec.competencies}
              onChange={(weights) => onChange(updateRole(spec, i, { ...role, weights }))}
            />
          </div>
        ))}
        <AddByName id="add-role" label="New role variant" onAdd={(name) => onChange(addRole(spec, name))} />
      </section>
    </div>
  );
}
