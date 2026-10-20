"use client";

import type { ReactNode } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Switch } from "@/components/ui/switch";
import {
  CAMERA_MODE_LABEL,
  CAMERA_MODES,
  INTEGRITY_MODE_LABEL,
  INTEGRITY_MODES,
  LIMITS,
  STRICTNESS,
  STRICTNESS_LABEL,
  TURN_MODE_LABEL,
  TURN_MODES,
  type Settings,
  type SettingsOverrides,
} from "@/lib/interviews";

const HINTS = {
  duration_minutes: `${LIMITS.duration_minutes.min} to ${LIMITS.duration_minutes.max} minutes.`,
  follow_up_depth: `Follow-ups per topic before moving on (${LIMITS.follow_up_depth.min} to ${LIMITS.follow_up_depth.max}).`,
  grading_runs: `Independent grading runs per topic (${LIMITS.grading_runs.min} to ${LIMITS.grading_runs.max}); the median is kept and disagreement is flagged.`,
  integrity_mode: "Browser signals (focus, full screen, copy/paste sizes) for a human reviewer. Never scored.",
  camera_monitoring: "Only the candidate's consent is collected for now; the on-device camera check comes later.",
  turn_mode: "How the interviewer's turns are produced.",
} as const;

function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function pick<T extends string>(options: readonly T[], value: string): T | undefined {
  return options.find((o) => o === value);
}

/** Every setting of a template (all values set). */
export function SettingsFields({
  idPrefix,
  value,
  onChange,
}: {
  idPrefix: string;
  value: Settings;
  onChange: (next: Settings) => void;
}) {
  const id = (name: string) => `${idPrefix}-${name}`;
  const update = (patch: Partial<Settings>) => onChange({ ...value, ...patch });
  const num = (n: number) => (Number.isFinite(n) ? n : "");

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field id={id("duration")} label="Duration (minutes)" hint={HINTS.duration_minutes}>
          <Input
            id={id("duration")}
            type="number"
            min={LIMITS.duration_minutes.min}
            max={LIMITS.duration_minutes.max}
            value={num(value.duration_minutes)}
            onChange={(e) => update({ duration_minutes: e.target.valueAsNumber })}
          />
        </Field>
        <Field id={id("depth")} label="Follow-up depth" hint={HINTS.follow_up_depth}>
          <Input
            id={id("depth")}
            type="number"
            min={LIMITS.follow_up_depth.min}
            max={LIMITS.follow_up_depth.max}
            value={num(value.follow_up_depth)}
            onChange={(e) => update({ follow_up_depth: e.target.valueAsNumber })}
          />
        </Field>
        <Field id={id("strictness")} label="Strictness">
          <NativeSelect
            id={id("strictness")}
            value={value.strictness}
            onChange={(e) => update({ strictness: pick(STRICTNESS, e.target.value) ?? value.strictness })}
          >
            {STRICTNESS.map((s) => (
              <option key={s} value={s}>
                {STRICTNESS_LABEL[s]}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Field id={id("language")} label="Language">
          <Input
            id={id("language")}
            value={value.language}
            maxLength={40}
            onChange={(e) => update({ language: e.target.value })}
          />
        </Field>
        <Field id={id("integrity")} label="Integrity monitoring" hint={HINTS.integrity_mode}>
          <NativeSelect
            id={id("integrity")}
            value={value.integrity_mode}
            onChange={(e) => update({ integrity_mode: pick(INTEGRITY_MODES, e.target.value) ?? value.integrity_mode })}
          >
            {INTEGRITY_MODES.map((m) => (
              <option key={m} value={m}>
                {INTEGRITY_MODE_LABEL[m]}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Field id={id("camera")} label="Camera check" hint={HINTS.camera_monitoring}>
          <NativeSelect
            id={id("camera")}
            value={value.camera_monitoring}
            onChange={(e) =>
              update({ camera_monitoring: pick(CAMERA_MODES, e.target.value) ?? value.camera_monitoring })
            }
          >
            {CAMERA_MODES.map((m) => (
              <option key={m} value={m}>
                {CAMERA_MODE_LABEL[m]}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Field id={id("runs")} label="Grading runs" hint={HINTS.grading_runs}>
          <Input
            id={id("runs")}
            type="number"
            min={LIMITS.grading_runs.min}
            max={LIMITS.grading_runs.max}
            value={num(value.grading_runs)}
            onChange={(e) => update({ grading_runs: e.target.valueAsNumber })}
          />
        </Field>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex items-center justify-between gap-3 rounded-lg border p-3 text-sm font-medium">
          Hints allowed
          <Switch checked={value.hints_allowed} onCheckedChange={(hints_allowed) => update({ hints_allowed })} />
        </label>
        <label className="flex items-center justify-between gap-3 rounded-lg border p-3 text-sm font-medium">
          <span className="flex flex-col">
            Blind grading
            <span className="text-xs font-normal text-muted-foreground">Graders see answers only, not the CV.</span>
          </span>
          <Switch checked={value.blind_grading} onCheckedChange={(blind_grading) => update({ blind_grading })} />
        </label>
      </div>
      <details className="rounded-lg border p-3">
        <summary className="cursor-pointer text-sm font-medium">Advanced</summary>
        <div className="mt-3 max-w-md">
          <Field id={id("turn-mode")} label="Turn mode" hint={HINTS.turn_mode}>
            <NativeSelect
              id={id("turn-mode")}
              value={value.turn_mode}
              onChange={(e) => update({ turn_mode: pick(TURN_MODES, e.target.value) ?? value.turn_mode })}
            >
              {TURN_MODES.map((m) => (
                <option key={m} value={m}>
                  {TURN_MODE_LABEL[m]}
                </option>
              ))}
            </NativeSelect>
          </Field>
        </div>
      </details>
    </div>
  );
}

function BooleanOverride({
  id,
  label,
  value,
  fallback,
  onChange,
}: {
  id: string;
  label: string;
  value: boolean | undefined;
  fallback: boolean;
  onChange: (next: boolean | undefined) => void;
}) {
  return (
    <Field id={id} label={label}>
      <NativeSelect
        id={id}
        value={value === undefined ? "" : value ? "yes" : "no"}
        onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value === "yes")}
      >
        <option value="">Default ({fallback ? "yes" : "no"})</option>
        <option value="yes">Yes</option>
        <option value="no">No</option>
      </NativeSelect>
    </Field>
  );
}

function NumberOverride({
  id,
  label,
  hint,
  limits,
  value,
  fallback,
  onChange,
}: {
  id: string;
  label: string;
  hint: string;
  limits: { min: number; max: number };
  value: number | undefined;
  fallback: number;
  onChange: (next: number | undefined) => void;
}) {
  return (
    <Field id={id} label={label} hint={hint}>
      <Input
        id={id}
        type="number"
        min={limits.min}
        max={limits.max}
        placeholder={`Default: ${fallback}`}
        value={value !== undefined && Number.isFinite(value) ? value : ""}
        onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.valueAsNumber)}
      />
    </Field>
  );
}

function ChoiceOverride<T extends string>({
  id,
  label,
  hint,
  options,
  labels,
  value,
  fallback,
  onChange,
}: {
  id: string;
  label: string;
  hint?: string;
  options: readonly T[];
  labels: Record<T, string>;
  value: T | undefined;
  /** The default's label, or null when another party (the organization) decides it. */
  fallback: string | null;
  onChange: (next: T | undefined) => void;
}) {
  return (
    <Field id={id} label={label} hint={hint}>
      <NativeSelect id={id} value={value ?? ""} onChange={(e) => onChange(pick(options, e.target.value))}>
        <option value="">{fallback === null ? "Organization default" : `Default (${fallback})`}</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {labels[o]}
          </option>
        ))}
      </NativeSelect>
    </Field>
  );
}

/**
 * Per-interview overrides: every field starts at "Default" (the template's value, or the platform's) and only the
 * ones the user changes are sent.
 */
export function OverrideFields({
  idPrefix,
  value,
  defaults,
  integrityFromOrg,
  onChange,
}: {
  idPrefix: string;
  value: SettingsOverrides;
  defaults: Settings;
  /** No template: the organization's policy decides the integrity mode. */
  integrityFromOrg: boolean;
  onChange: (next: SettingsOverrides) => void;
}) {
  const id = (name: string) => `${idPrefix}-${name}`;
  const update = (patch: SettingsOverrides) => onChange({ ...value, ...patch });

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <NumberOverride
          id={id("duration")}
          label="Duration (minutes)"
          hint={HINTS.duration_minutes}
          limits={LIMITS.duration_minutes}
          value={value.duration_minutes}
          fallback={defaults.duration_minutes}
          onChange={(duration_minutes) => update({ duration_minutes })}
        />
        <NumberOverride
          id={id("depth")}
          label="Follow-up depth"
          hint={HINTS.follow_up_depth}
          limits={LIMITS.follow_up_depth}
          value={value.follow_up_depth}
          fallback={defaults.follow_up_depth}
          onChange={(follow_up_depth) => update({ follow_up_depth })}
        />
        <ChoiceOverride
          id={id("strictness")}
          label="Strictness"
          options={STRICTNESS}
          labels={STRICTNESS_LABEL}
          value={value.strictness}
          fallback={STRICTNESS_LABEL[defaults.strictness]}
          onChange={(strictness) => update({ strictness })}
        />
        <BooleanOverride
          id={id("hints")}
          label="Hints allowed"
          value={value.hints_allowed}
          fallback={defaults.hints_allowed}
          onChange={(hints_allowed) => update({ hints_allowed })}
        />
        <ChoiceOverride
          id={id("integrity")}
          label="Integrity monitoring"
          hint={HINTS.integrity_mode}
          options={INTEGRITY_MODES}
          labels={INTEGRITY_MODE_LABEL}
          value={value.integrity_mode}
          fallback={integrityFromOrg ? null : INTEGRITY_MODE_LABEL[defaults.integrity_mode]}
          onChange={(integrity_mode) => update({ integrity_mode })}
        />
        <ChoiceOverride
          id={id("camera")}
          label="Camera check"
          hint={HINTS.camera_monitoring}
          options={CAMERA_MODES}
          labels={CAMERA_MODE_LABEL}
          value={value.camera_monitoring}
          fallback={CAMERA_MODE_LABEL[defaults.camera_monitoring]}
          onChange={(camera_monitoring) => update({ camera_monitoring })}
        />
        <NumberOverride
          id={id("runs")}
          label="Grading runs"
          hint={HINTS.grading_runs}
          limits={LIMITS.grading_runs}
          value={value.grading_runs}
          fallback={defaults.grading_runs}
          onChange={(grading_runs) => update({ grading_runs })}
        />
      </div>
      <details className="rounded-lg border p-3">
        <summary className="cursor-pointer text-sm font-medium">Advanced</summary>
        <div className="mt-3 max-w-md">
          <ChoiceOverride
            id={id("turn-mode")}
            label="Turn mode"
            hint={HINTS.turn_mode}
            options={TURN_MODES}
            labels={TURN_MODE_LABEL}
            value={value.turn_mode}
            fallback={TURN_MODE_LABEL[defaults.turn_mode]}
            onChange={(turn_mode) => update({ turn_mode })}
          />
        </div>
      </details>
    </div>
  );
}
