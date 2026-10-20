"use client";

import { CircleCheck, LoaderCircle, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { useSpaces } from "@/lib/knowledge-api";
import { toggleId } from "@/lib/questions";
import { DRAFT_STORAGE_KEY, draftYaml, encodeDraft, parseSpec, type BuildOut, type RolePack } from "@/lib/rolepacks";
import { useBuildPack, useCreatePack } from "@/lib/rolepacks-api";

import { PackDiffView } from "./pack-diff";
import { ProblemsList } from "./problems-list";

const MIN_DESCRIPTION = 10;
const MAX_DESCRIPTION = 4000;

/** Seconds since `running` became true, for the progress line of a long agent call. */
function useElapsedSeconds(running: boolean): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!running) return;
    const started = Date.now();
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => {
      clearInterval(timer);
      setSeconds(0);
    };
  }, [running]);
  return seconds;
}

function DraftResult({
  result,
  basePack,
  onReset,
  onClose,
}: {
  result: BuildOut;
  basePack: RolePack | undefined;
  onReset: () => void;
  onClose: () => void;
}) {
  const router = useRouter();
  const create = useCreatePack();
  const spec = parseSpec(result.spec);

  function openInEditor() {
    try {
      sessionStorage.setItem(DRAFT_STORAGE_KEY, encodeDraft(draftYaml(result)));
    } catch {
      // Storage full or disabled: the editor explains that no draft was found.
    }
    onClose();
    router.push("/app/role-packs/new");
  }

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-semibold">{spec?.name || "Draft role pack"}</h3>
        {result.valid ? (
          <Badge variant="success">
            <CircleCheck aria-hidden /> valid
          </Badge>
        ) : (
          <Badge variant="destructive">needs fixes</Badge>
        )}
        {spec && (
          <Badge variant="outline">
            {spec.competencies.length} competencies · {spec.roles.length} roles
          </Badge>
        )}
      </div>
      {spec?.description && <p className="text-sm text-muted-foreground">{spec.description}</p>}
      {!result.valid && <ProblemsList problems={result.problems} spec={spec} />}
      {result.diff && basePack && <PackDiffView diff={result.diff} baseName={basePack.name} />}
      <div className="flex flex-col gap-1">
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">YAML preview</p>
        <pre className="max-h-72 overflow-auto rounded-md border bg-muted/40 p-3 font-mono text-xs leading-relaxed">
          {draftYaml(result)}
        </pre>
      </div>
      <DialogFooter>
        <Button variant="ghost" onClick={onReset}>
          Start over
        </Button>
        <Button variant="outline" onClick={openInEditor}>
          Open in editor
        </Button>
        {result.valid && result.yaml && (
          <Button
            disabled={create.isPending}
            onClick={() =>
              result.yaml &&
              create.mutate(
                { yaml: result.yaml },
                {
                  onSuccess: (pack) => {
                    onClose();
                    router.push(`/app/role-packs/${pack.id}`);
                  },
                },
              )
            }
          >
            {create.isPending ? "Saving…" : "Save as my pack"}
          </Button>
        )}
      </DialogFooter>
    </div>
  );
}

/** Role Builder: describe a role in plain language and get a draft pack to review, save or edit. */
export function BuilderDialog({ packs }: { packs: readonly RolePack[] }) {
  const [open, setOpen] = useState(false);
  const [description, setDescription] = useState("");
  const [spaceIds, setSpaceIds] = useState<string[]>([]);
  const [baseId, setBaseId] = useState("");
  const build = useBuildPack();
  const spaces = useSpaces();
  const elapsed = useElapsedSeconds(build.isPending);
  const basePack = packs.find((p) => p.id === baseId);
  const trimmed = description.trim();

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next && !build.isPending) build.reset();
      }}
    >
      <DialogTrigger asChild>
        <Button>
          <Sparkles /> Build with AI
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Role Builder</DialogTitle>
          <DialogDescription>
            Describe the role in plain language. The AI drafts competencies with anchored levels, role variants and
            weights; you review the draft before anything is saved.
          </DialogDescription>
        </DialogHeader>

        {build.data ? (
          <DraftResult
            result={build.data}
            basePack={basePack}
            onReset={() => build.reset()}
            onClose={() => {
              setOpen(false);
              build.reset();
            }}
          />
        ) : (
          <form
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              build.mutate({
                description: trimmed,
                space_ids: spaceIds.length > 0 ? spaceIds : null,
                base_pack_id: baseId || null,
              });
            }}
          >
            <fieldset disabled={build.isPending} className="flex flex-col gap-4">
              <div className="flex flex-col gap-2">
                <Label htmlFor="builder-description">The role</Label>
                <Textarea
                  id="builder-description"
                  rows={6}
                  maxLength={MAX_DESCRIPTION}
                  value={description}
                  placeholder="Senior firmware engineer for battery-powered BLE sensors: low-power design, Zephyr RTOS, OTA updates, production test…"
                  onChange={(e) => setDescription(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  Seniority, products, must-have skills and what separates a strong hire. At least {MIN_DESCRIPTION}{" "}
                  characters.
                </p>
              </div>

              {spaces.data && spaces.data.length > 0 && (
                <fieldset className="flex flex-col gap-2">
                  <legend className="mb-2 text-sm font-medium">Ground it in knowledge spaces (optional)</legend>
                  <div className="grid max-h-40 gap-2 overflow-y-auto sm:grid-cols-2">
                    {spaces.data.map((space) => (
                      <label key={space.id} className="flex items-center gap-2 text-sm">
                        <Checkbox
                          checked={spaceIds.includes(space.id)}
                          onCheckedChange={() => setSpaceIds((ids) => toggleId(ids, space.id))}
                        />
                        <span className="truncate">{space.name}</span>
                      </label>
                    ))}
                  </div>
                </fieldset>
              )}

              <div className="flex flex-col gap-2">
                <Label htmlFor="builder-base">Compare with (optional)</Label>
                <NativeSelect id="builder-base" value={baseId} onChange={(e) => setBaseId(e.target.value)}>
                  <option value="">No comparison</option>
                  {packs.map((pack) => (
                    <option key={pack.id} value={pack.id}>
                      {pack.name}
                    </option>
                  ))}
                </NativeSelect>
                <p className="text-xs text-muted-foreground">
                  Shows which competencies, roles and weights the draft adds, removes or changes.
                </p>
              </div>
            </fieldset>

            {build.isPending && (
              <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
                <LoaderCircle className="size-4 animate-spin" aria-hidden />
                Drafting the role pack… {elapsed}s. This usually takes 10 to 60 seconds.
              </p>
            )}
            <DialogFooter>
              <Button type="submit" disabled={trimmed.length < MIN_DESCRIPTION || build.isPending}>
                {build.isPending ? "Drafting…" : "Draft role pack"}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
