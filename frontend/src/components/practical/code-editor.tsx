"use client";

// Loaded only in the browser (next/dynamic with ssr: false). Monaco is bundled from the installed `monaco-editor`
// package and handed to @monaco-editor/react, so nothing is fetched from a CDN.
import Editor, { loader, type OnMount } from "@monaco-editor/react";
import * as monaco from "monaco-editor/editor";
import "monaco-editor/features/register.all";
import "monaco-editor/languages/definitions/cpp/register";
import "monaco-editor/languages/definitions/python/register";
import "monaco-editor/languages/definitions/rust/register";
import { useEffect, useRef } from "react";

import { usePrefersDark } from "@/lib/use-prefers-dark";

globalThis.MonacoEnvironment = {
  getWorker: () => new Worker(new URL("./monaco.worker.ts", import.meta.url), { type: "module" }),
};
loader.config({ monaco });

export interface CodeEditorProps {
  value: string;
  language: string;
  readOnly?: boolean;
  /** Characters inserted by a paste (an integrity signal: the size only, never the content). */
  onPaste?: (chars: number) => void;
  onChange?: (value: string) => void;
  label: string;
}

export default function CodeEditor({ value, language, readOnly = false, onPaste, onChange, label }: CodeEditorProps) {
  const pasteRef = useRef(onPaste);
  const dark = usePrefersDark();

  useEffect(() => {
    pasteRef.current = onPaste;
  });

  const onMount: OnMount = (editor) => {
    editor.onDidPaste((event) => {
      const model = editor.getModel();
      if (model) pasteRef.current?.(model.getValueLengthInRange(event.range));
    });
  };

  return (
    <Editor
      height="24rem"
      language={language}
      value={value}
      theme={dark ? "vs-dark" : "vs"}
      onChange={(next) => onChange?.(next ?? "")}
      onMount={onMount}
      loading={<p className="p-3 text-sm text-muted-foreground">Loading the editor…</p>}
      options={{
        readOnly,
        ariaLabel: label,
        minimap: { enabled: false },
        fontSize: 13,
        tabSize: 4,
        insertSpaces: true,
        scrollBeyondLastLine: false,
        automaticLayout: true,
        wordWrap: "off",
        renderWhitespace: "selection",
        // No inline AI-style help or suggestions from the editor itself during an interview.
        quickSuggestions: false,
        suggestOnTriggerCharacters: false,
        wordBasedSuggestions: "off",
        inlineSuggest: { enabled: false },
        parameterHints: { enabled: false },
      }}
    />
  );
}
