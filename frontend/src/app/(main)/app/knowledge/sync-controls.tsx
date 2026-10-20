"use client";

import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Switch } from "@/components/ui/switch";
import { intervalOptions, toInterval } from "@/lib/connectors";

export function IntervalSelect({
  id,
  value,
  onChange,
  disabled,
  label = "Sync interval",
}: {
  id?: string;
  value: number;
  onChange: (minutes: number) => void;
  disabled?: boolean;
  label?: string;
}) {
  return (
    <NativeSelect
      id={id}
      aria-label={id ? undefined : label}
      value={String(value)}
      disabled={disabled}
      onChange={(e) => onChange(toInterval(e.target.value))}
    >
      {intervalOptions(value).map((o) => (
        <option key={o.minutes} value={o.minutes}>
          {o.label}
        </option>
      ))}
    </NativeSelect>
  );
}

/** "Keep in sync" switch with its interval: a scheduled source re-checks for new, changed and deleted files. */
export function SyncControls({
  idPrefix,
  enabled,
  onEnabledChange,
  intervalMinutes,
  onIntervalChange,
  help = "Checks again on a schedule: new and changed files are imported, removed ones are deleted here too.",
}: {
  idPrefix: string;
  enabled: boolean;
  onEnabledChange: (enabled: boolean) => void;
  intervalMinutes: number;
  onIntervalChange: (minutes: number) => void;
  help?: string;
}) {
  return (
    <div className="flex flex-col gap-2 rounded-md border p-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Label htmlFor={`${idPrefix}-sync`}>Keep in sync</Label>
        <Switch id={`${idPrefix}-sync`} checked={enabled} onCheckedChange={onEnabledChange} />
      </div>
      <p className="text-xs text-muted-foreground">{help}</p>
      {enabled && (
        <div className="flex flex-wrap items-center gap-2">
          <Label htmlFor={`${idPrefix}-interval`} className="font-normal">
            Check
          </Label>
          <IntervalSelect id={`${idPrefix}-interval`} value={intervalMinutes} onChange={onIntervalChange} />
        </div>
      )}
    </div>
  );
}
