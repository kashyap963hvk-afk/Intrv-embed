"use client";

import { useState } from "react";

import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { linesToList, listToLines, sameList } from "@/lib/rolepacks";

/**
 * A list of short texts edited as one per line (or comma-separated). The typed text is kept as is, so a trailing
 * newline or comma survives while typing; the list only resyncs when it changes from outside.
 */
export function ListField({
  id,
  value,
  onChange,
  separator = "\n",
  rows = 3,
  placeholder,
}: {
  id: string;
  value: readonly string[];
  onChange: (next: string[]) => void;
  separator?: "\n" | ",";
  rows?: number;
  placeholder?: string;
}) {
  const [text, setText] = useState(() => listToLines(value, separator));
  const [prev, setPrev] = useState(value);
  if (prev !== value) {
    setPrev(value);
    if (!sameList(linesToList(text, separator), value)) setText(listToLines(value, separator));
  }

  function change(next: string) {
    setText(next);
    const list = linesToList(next, separator);
    if (!sameList(list, value)) onChange(list);
  }

  if (separator === ",") {
    return <Input id={id} value={text} placeholder={placeholder} onChange={(e) => change(e.target.value)} />;
  }
  return (
    <Textarea
      id={id}
      rows={Math.max(rows, Math.min(10, value.length + 1))}
      value={text}
      placeholder={placeholder}
      onChange={(e) => change(e.target.value)}
    />
  );
}
