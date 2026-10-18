"use client";

import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { DOC_TYPES, toDocTypeChoice, type DocTypeChoice } from "@/lib/knowledge";

export function DocTypeSelect({
  id,
  value,
  onChange,
}: {
  id: string;
  value: DocTypeChoice;
  onChange: (value: DocTypeChoice) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      <Label htmlFor={id}>Document type</Label>
      <NativeSelect id={id} value={value} onChange={(e) => onChange(toDocTypeChoice(e.target.value))}>
        <option value="AUTO">Auto-detect</option>
        {DOC_TYPES.map((t) => (
          <option key={t.value} value={t.value}>
            {t.label}
          </option>
        ))}
      </NativeSelect>
    </div>
  );
}
