"use client";

import { useCallback, useState } from "react";

function read(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    window.sessionStorage.setItem(key, value);
  } catch {
    // Storage can be unavailable (private windows, blocked site data): the draft just isn't kept.
  }
}

/**
 * A text draft kept in this tab's session storage, so a reload during a practical task does not lose the work.
 * It never leaves the browser (the candidate submits explicitly).
 */
export function useSessionDraft(key: string, initial: string): [string, (value: string) => void] {
  const [value, setValue] = useState(() => (typeof window === "undefined" ? initial : (read(key) ?? initial)));
  const update = useCallback(
    (next: string) => {
      setValue(next);
      write(key, next);
    },
    [key],
  );
  return [value, update];
}
