"use client";

import { Copy, Link2 } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { DEFAULT_INVITE_HOURS, INVITE_EXPIRY, type InterviewDetail, type InviteOut } from "@/lib/interviews";
import { useInvite } from "@/lib/interviews-api";

/** Create or rotate the candidate's link. Only a hash is stored, so the URL is shown once, right here. */
export function InvitePanel({ interview }: { interview: InterviewDetail }) {
  const invite = useInvite(interview.id);
  const [link, setLink] = useState<InviteOut | null>(null);
  const [hours, setHours] = useState<number>(DEFAULT_INVITE_HOURS);
  const field = useRef<HTMLInputElement>(null);
  const existing = interview.invite_expires_at;

  async function copy(url: string) {
    try {
      await navigator.clipboard.writeText(url);
      toast.success("Link copied");
    } catch {
      field.current?.select();
      toast.message("Select the link and copy it");
    }
  }

  function create() {
    if (existing && !window.confirm("Create a new link? The current link stops working.")) return;
    invite.mutate(hours, { onSuccess: setLink });
  }

  return (
    <Card>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <h2 className="flex items-center gap-2 font-semibold">
            <Link2 className="size-4" aria-hidden /> Invite the candidate
          </h2>
          <p className="text-sm text-muted-foreground">
            The candidate opens the link without an account, reads what is recorded, gives consent and starts the
            interview.
          </p>
        </div>
        {link ? (
          <div className="flex flex-col gap-2">
            <Label htmlFor="invite-url">Candidate link</Label>
            <div className="flex gap-2">
              <Input id="invite-url" ref={field} readOnly value={link.url} onFocus={(e) => e.target.select()} />
              <Button type="button" variant="outline" onClick={() => void copy(link.url)}>
                <Copy /> Copy
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              Copy it now: for privacy the link is shown only once. It expires{" "}
              {new Date(link.expires_at).toLocaleString()}.
            </p>
          </div>
        ) : (
          existing && (
            <p className="text-sm text-muted-foreground">
              A link was created and expires {new Date(existing).toLocaleString()}. It cannot be shown again; create a
              new one if you need it (the old one then stops working).
            </p>
          )
        )}
        <div className="flex flex-wrap items-end gap-2">
          <div className="flex flex-col gap-2">
            <Label htmlFor="invite-expiry">Valid for</Label>
            <NativeSelect id="invite-expiry" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
              {INVITE_EXPIRY.map((option) => (
                <option key={option.hours} value={option.hours}>
                  {option.label}
                </option>
              ))}
            </NativeSelect>
          </div>
          <Button variant={existing || link ? "outline" : "default"} disabled={invite.isPending} onClick={create}>
            {invite.isPending ? "Creating…" : existing || link ? "Create a new link" : "Create candidate link"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
