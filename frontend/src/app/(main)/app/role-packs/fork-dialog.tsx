"use client";

import { GitFork } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { RolePack } from "@/lib/rolepacks";
import { useForkPack } from "@/lib/rolepacks-api";

/** Copy a pack into a private one the user can edit, then open it in the editor. */
export function ForkDialog({
  pack,
  label = "Fork",
  variant = "ghost",
}: {
  pack: Pick<RolePack, "id" | "name">;
  label?: string;
  variant?: "ghost" | "outline" | "default";
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const fork = useForkPack();
  const router = useRouter();

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) setName(`${pack.name} (copy)`.slice(0, 120));
      }}
    >
      <DialogTrigger asChild>
        <Button size="sm" variant={variant}>
          <GitFork /> {label}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Fork {pack.name}</DialogTitle>
          <DialogDescription>
            Makes a private copy you can edit. The original stays as it is; publish your copy later to share it.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            fork.mutate(
              { pack, name: name.trim() || null },
              {
                onSuccess: (copy) => {
                  setOpen(false);
                  router.push(`/app/role-packs/${copy.id}`);
                },
              },
            );
          }}
        >
          <div className="flex flex-col gap-2">
            <Label htmlFor={`fork-name-${pack.id}`}>Name of the copy</Label>
            <Input id={`fork-name-${pack.id}`} value={name} maxLength={120} onChange={(e) => setName(e.target.value)} />
          </div>
          <DialogFooter>
            <Button type="submit" disabled={fork.isPending}>
              {fork.isPending ? "Forking…" : "Fork and edit"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
