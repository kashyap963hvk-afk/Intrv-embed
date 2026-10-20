"use client";

import { Save } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { parseSpec, problemsFromError, toYaml, type PackIn, type PackSpec } from "@/lib/rolepacks";
import { useValidation } from "@/lib/rolepacks-api";
import { useDebouncedValue } from "@/lib/use-debounced-value";

import { ProblemsList } from "./problems-list";
import { SpecForm } from "./spec-form";

const VALIDATE_DEBOUNCE_MS = 500;

export type EditorTab = "form" | "yaml";

interface SpecEditorProps {
  /** The spec the form starts from; null when the content starts as YAML only (a Role Builder draft). */
  initialSpec: PackSpec | null;
  initialYaml: string;
  readOnly: boolean;
  tab: EditorTab;
  onTabChange: (tab: EditorTab) => void;
  saveLabel: string;
  saving: boolean;
  /** The last save's error; its spec problems are listed with the live ones. */
  saveError: unknown;
  onSave: (body: PackIn, onSaved: () => void) => void;
  /** Whether the content can be saved before any edit (a draft that does not exist yet). */
  savePristine?: boolean;
}

/**
 * The role pack editor: a Form tab and a YAML tab over the same content, live validation (debounced) and save.
 * Whichever tab was edited last is what gets saved: form edits regenerate the YAML, and YAML edits reach the form
 * once they validate.
 */
export function SpecEditor({
  initialSpec,
  initialYaml,
  readOnly,
  tab,
  onTabChange,
  saveLabel,
  saving,
  saveError,
  onSave,
  savePristine = false,
}: SpecEditorProps) {
  const [spec, setSpec] = useState<PackSpec | null>(initialSpec);
  const [yamlText, setYamlText] = useState(initialYaml);
  const [source, setSource] = useState<"form" | "yaml">(initialSpec ? "form" : "yaml");
  const [dirty, setDirty] = useState(false);

  const body = useMemo<PackIn | null>(() => {
    if (readOnly) return null;
    return source === "yaml" || !spec ? { yaml: yamlText } : { spec };
  }, [readOnly, source, spec, yamlText]);
  const debounced = useDebouncedValue(body, VALIDATE_DEBOUNCE_MS);
  const validation = useValidation(debounced);
  const settled = debounced === body && !validation.isFetching && !validation.isPlaceholderData;
  const current = settled ? validation.data : undefined;
  const problems = validation.data?.problems ?? [];
  const saveProblems = problemsFromError(saveError);

  const formSpec = useMemo(
    () => (source === "form" ? spec : current?.valid ? parseSpec(current.spec) : null),
    [source, spec, current],
  );

  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  function editForm(next: PackSpec) {
    setSpec(next);
    setYamlText(toYaml(next));
    setSource("form");
    setDirty(true);
  }

  function editYaml(next: string) {
    setYamlText(next);
    setSource("yaml");
    setDirty(true);
  }

  const knownInvalid = current !== undefined && !current.valid;
  const canSave = !readOnly && (dirty || savePristine) && !saving && !knownInvalid && body !== null;

  return (
    <div className="flex flex-col gap-4">
      <Tabs value={tab} onValueChange={(value) => onTabChange(value === "yaml" ? "yaml" : "form")}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <TabsList>
            <TabsTrigger value="form">Form</TabsTrigger>
            <TabsTrigger value="yaml">YAML</TabsTrigger>
          </TabsList>
          {!readOnly && (
            <div className="flex items-center gap-3">
              {dirty && <span className="text-xs text-muted-foreground">Unsaved changes</span>}
              <Button
                disabled={!canSave}
                onClick={() => body && onSave(body, () => setDirty(false))}
                title={knownInvalid ? "Fix the problems listed below first" : undefined}
              >
                <Save /> {saving ? "Saving…" : saveLabel}
              </Button>
            </div>
          )}
        </div>

        {!readOnly && (
          <ProblemsList
            problems={problems.length > 0 ? problems : saveProblems}
            spec={formSpec ?? spec}
            checking={!settled}
          />
        )}

        <TabsContent value="form">
          {formSpec ? (
            <fieldset disabled={readOnly} className="min-w-0">
              <SpecForm spec={formSpec} onChange={editForm} />
            </fieldset>
          ) : (
            <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
              {settled
                ? "The YAML has problems the form cannot show. Fix them in the YAML tab to edit here."
                : "Checking the YAML…"}
            </p>
          )}
        </TabsContent>

        <TabsContent value="yaml">
          <Textarea
            aria-label="Role pack YAML"
            className="min-h-[32rem] font-mono text-xs leading-relaxed whitespace-pre"
            spellCheck={false}
            wrap="off"
            readOnly={readOnly}
            value={yamlText}
            onChange={(e) => editYaml(e.target.value)}
            onKeyDown={(e) => {
              // Tab indents (two spaces) instead of leaving the editor; Shift+Tab still moves focus.
              if (readOnly || e.key !== "Tab" || e.shiftKey || e.altKey || e.ctrlKey || e.metaKey) return;
              e.preventDefault();
              const area = e.currentTarget;
              area.setRangeText("  ", area.selectionStart, area.selectionEnd, "end");
              editYaml(area.value);
            }}
          />
          {!readOnly && (
            <p className="mt-2 text-xs text-muted-foreground">
              Schema: docs/DOMAIN_PACK_EMBEDDED.md §1. Checked as you type; problems are listed above.
            </p>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
