"use client";

import { useQuery } from "@tanstack/react-query";
import { Download, Search, ShieldAlert, Trash } from "lucide-react";
import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useAdminMutation } from "@/lib/admin-api";
import { api, type Schemas } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import {
  buildSubjectIn,
  countSummary,
  describeSubject,
  EMPTY_SUBJECT_FORM,
  ERASE_CONFIRMATION,
  ERASE_REASON_MAX,
  eraseConfirmed,
  eraseReasonProblem,
  groupSubjectItems,
  itemCounts,
  notErased,
  RETENTION_LIMITS,
  retentionCutoff,
  retentionProblem,
  subjectFormProblem,
  type EraseOut,
  type SubjectForm,
  type SubjectIn,
  type SubjectItem,
} from "@/lib/privacy";
import { useSubjectErase, useSubjectExport, useSubjectSearch } from "@/lib/privacy-api";

type Policy = Schemas["OrgPolicy"];

const ORG_POLICY_KEY = ["admin", "org-policy"] as const;

function SearchForm({ onSearch, pending }: { onSearch: (subject: SubjectIn) => void; pending: boolean }) {
  const [form, setForm] = useState<SubjectForm>(EMPTY_SUBJECT_FORM);
  const [touched, setTouched] = useState(false);
  const problem = subjectFormProblem(form);
  const update = (patch: Partial<SubjectForm>) => setForm((current) => ({ ...current, ...patch }));

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        setTouched(true);
        if (!problem) onSearch(buildSubjectIn(form));
      }}
    >
      <div className="flex flex-wrap items-end gap-2">
        <div className="flex min-w-64 flex-1 flex-col gap-1.5">
          <Label htmlFor="privacy-email">Candidate email (as written on their CV)</Label>
          <Input
            id="privacy-email"
            type="email"
            autoComplete="off"
            value={form.email}
            placeholder="candidate@example.com"
            onChange={(e) => update({ email: e.target.value })}
          />
        </div>
        <Button type="submit" disabled={pending}>
          <Search aria-hidden /> {pending ? "Searching…" : "Find their data"}
        </Button>
      </div>
      <details className="text-sm">
        <summary className="cursor-pointer text-muted-foreground">
          Or search by interview, candidate profile or CV document ids
        </summary>
        <div className="mt-2 grid gap-3 md:grid-cols-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="privacy-sessions">Interview ids</Label>
            <Textarea
              id="privacy-sessions"
              rows={3}
              className="font-mono text-xs"
              value={form.sessionIds}
              onChange={(e) => update({ sessionIds: e.target.value })}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="privacy-profiles">Candidate profile ids</Label>
            <Textarea
              id="privacy-profiles"
              rows={3}
              className="font-mono text-xs"
              value={form.profileIds}
              onChange={(e) => update({ profileIds: e.target.value })}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="privacy-documents">CV document ids</Label>
            <Textarea
              id="privacy-documents"
              rows={3}
              className="font-mono text-xs"
              value={form.documentIds}
              onChange={(e) => update({ documentIds: e.target.value })}
            />
          </div>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          Separate ids with commas or new lines. A CV brings its profile, interviews and question sets along.
        </p>
      </details>
      {touched && problem && <p className="text-sm text-destructive">{problem}</p>}
    </form>
  );
}

function HeldItems({ items }: { items: readonly SubjectItem[] }) {
  return (
    <div className="flex flex-col gap-3">
      {groupSubjectItems(items).map((group) => (
        <div key={group.kind} className="flex flex-col gap-1">
          <h4 className="flex items-center gap-2 text-sm font-medium">
            {group.title} <Badge variant="secondary">{group.items.length}</Badge>
          </h4>
          {group.holds && <p className="text-xs text-muted-foreground">Holds {group.holds}.</p>}
          <ul className="flex flex-col gap-0.5 text-sm">
            {group.items.map((item) => (
              <li key={item.id} className="flex flex-wrap items-baseline gap-x-2">
                <span>{item.label || "Untitled"}</span>
                <span className="font-mono text-xs text-muted-foreground">{item.id}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function EraseDialog({
  open,
  onOpenChange,
  subject,
  items,
  onErased,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  subject: SubjectIn;
  items: readonly SubjectItem[];
  onErased: (result: EraseOut) => void;
}) {
  const [typed, setTyped] = useState("");
  const [reason, setReason] = useState("");
  const erase = useSubjectErase();
  const reasonProblem = eraseReasonProblem(reason);
  const confirmed = eraseConfirmed(typed) && !reasonProblem;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (erase.isPending) return;
        onOpenChange(next);
        if (!next) {
          setTyped("");
          setReason("");
        }
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-destructive">
            <ShieldAlert className="size-5" aria-hidden /> Permanently erase this candidate&apos;s data
          </DialogTitle>
          <DialogDescription>{describeSubject(subject)}</DialogDescription>
        </DialogHeader>
        <Alert variant="destructive">
          <AlertTitle>This cannot be undone</AlertTitle>
          <AlertDescription>
            <p>
              {countSummary(itemCounts(items))} will be deleted for every member of the organization: database records,
              stored files (CVs, whiteboard images, camera snapshots) and search index entries. Reports and decisions
              for these interviews are gone afterwards.
            </p>
            <p>
              The server runs the same search again when you confirm and erases whatever matches at that moment. Export
              first if the candidate asked for a copy.
            </p>
          </AlertDescription>
        </Alert>
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (!confirmed) return;
            erase.mutate(
              { subject, reason },
              {
                onSuccess: (result) => {
                  setTyped("");
                  setReason("");
                  onErased(result);
                },
              },
            );
          }}
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="erase-reason">Reason (e.g. request reference)</Label>
            <Input
              id="erase-reason"
              autoComplete="off"
              maxLength={ERASE_REASON_MAX}
              value={reason}
              disabled={erase.isPending}
              placeholder="Erasure request #2026-114 by email"
              onChange={(e) => setReason(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Optional. Kept in the audit log with who erased what and when.
            </p>
            {reasonProblem && <p className="text-xs text-destructive">{reasonProblem}</p>}
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="erase-confirm">
              Type <span className="font-mono font-semibold">{ERASE_CONFIRMATION}</span> to confirm
            </Label>
            <Input
              id="erase-confirm"
              autoComplete="off"
              value={typed}
              disabled={erase.isPending}
              onChange={(e) => setTyped(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" disabled={erase.isPending} onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="destructive" disabled={!confirmed || erase.isPending}>
              <Trash aria-hidden /> {erase.isPending ? "Erasing…" : "Erase permanently"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function SubjectCard() {
  const search = useSubjectSearch();
  const exporter = useSubjectExport();
  const [subject, setSubject] = useState<SubjectIn | null>(null);
  const [eraseOpen, setEraseOpen] = useState(false);
  const [erased, setErased] = useState<EraseOut | null>(null);
  const items = subject ? (search.data ?? []) : [];

  function find(next: SubjectIn) {
    setErased(null);
    search.mutate(next, { onSuccess: () => setSubject(next) });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Candidate data</CardTitle>
        <CardDescription className="max-w-3xl">
          Find everything held about one candidate across every member&apos;s interviews, CVs and question sets. Export
          it to answer a subject access request, or erase it to honour a right-to-erasure request. Every export and
          erasure is recorded in the audit log (the email only as a hash).
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <SearchForm onSearch={find} pending={search.isPending} />

        {erased && (
          <Alert>
            <AlertTitle>Erased {countSummary(erased.erased)}</AlertTitle>
            <AlertDescription>
              {notErased(erased.items, erased.erased) > 0
                ? `${notErased(erased.items, erased.erased)} matching item(s) were already gone.`
                : "Nothing that matched is left."}
            </AlertDescription>
          </Alert>
        )}

        {subject && !erased && search.data && (
          <section className="flex flex-col gap-4 rounded-md border p-4" aria-live="polite">
            <div className="flex flex-col gap-0.5">
              <h3 className="font-medium">
                {items.length === 0 ? "Nothing found" : `Held: ${countSummary(itemCounts(items))}`}
              </h3>
              <p className="text-xs text-muted-foreground">For {describeSubject(subject)}</p>
            </div>
            {items.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No interview, CV, profile or question set matches. Email search compares the address on each
                candidate&apos;s CV profile (ignoring case); try their interview or CV ids if the CV had no email.
              </p>
            ) : (
              <>
                <HeldItems items={items} />
                <div className="flex flex-wrap gap-2 border-t pt-4">
                  <Button variant="outline" disabled={exporter.isPending} onClick={() => exporter.mutate(subject)}>
                    <Download aria-hidden /> {exporter.isPending ? "Preparing…" : "Export (JSON)"}
                  </Button>
                  <Button variant="destructive" onClick={() => setEraseOpen(true)}>
                    <Trash aria-hidden /> Erase everything…
                  </Button>
                </div>
                <EraseDialog
                  open={eraseOpen}
                  onOpenChange={setEraseOpen}
                  subject={subject}
                  items={items}
                  onErased={(result) => {
                    setEraseOpen(false);
                    setErased(result);
                  }}
                />
              </>
            )}
          </section>
        )}
      </CardContent>
    </Card>
  );
}

function RetentionForm({ policy }: { policy: Policy }) {
  const current = policy.retention_days ?? 180;
  const [days, setDays] = useState(String(current));
  const value = Number(days);
  const problem = retentionProblem(value);
  const save = useAdminMutation(
    async () => unwrap(await api.PUT("/api/admin/org-policy", { body: { ...policy, retention_days: value } })),
    (saved) => `Candidate data is now kept for ${saved.retention_days ?? value} days`,
  );
  const cutoff = retentionCutoff(current, new Date());

  return (
    <form
      className="flex flex-col gap-3 text-sm"
      onSubmit={(e) => {
        e.preventDefault();
        if (!problem) save.mutate(undefined);
      }}
    >
      <div className="flex flex-wrap items-end gap-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="retention-days">Keep candidate data for (days)</Label>
          <Input
            id="retention-days"
            type="number"
            className="w-32"
            min={RETENTION_LIMITS.min}
            max={RETENTION_LIMITS.max}
            step={1}
            value={days}
            onChange={(e) => setDays(e.target.value)}
          />
        </div>
        <Button type="submit" disabled={Boolean(problem) || value === current || save.isPending}>
          {save.isPending ? "Saving…" : "Save"}
        </Button>
      </div>
      {problem && <p className="text-destructive">{problem}</p>}
      {value < current && !problem && (
        <p className="text-destructive">
          Shortening the period makes tonight&apos;s purge erase everything older than {value} days. That cannot be
          undone.
        </p>
      )}
      <ul className="list-disc space-y-0.5 pl-5 text-muted-foreground">
        <li>
          A nightly job erases interviews that ended (or last changed) more than {current} days ago, and question sets
          and CVs last changed before then. A CV is kept while an interview or question set still uses it.
        </li>
        <li>
          At the next run, that means anything inactive since before {cutoff.toLocaleDateString()}. Live interviews are
          never purged.
        </li>
        <li>Candidates see this period before they consent to an interview.</li>
      </ul>
    </form>
  );
}

function RetentionCard() {
  const policy = useQuery({
    queryKey: ORG_POLICY_KEY,
    queryFn: async () => unwrap(await api.GET("/api/admin/org-policy")),
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Retention</CardTitle>
        <CardDescription>How long candidate data is kept before it is erased automatically.</CardDescription>
      </CardHeader>
      <CardContent>
        {policy.isError && <p className="text-sm text-destructive">{policy.error.message}</p>}
        {policy.data ? (
          <RetentionForm key={policy.dataUpdatedAt} policy={policy.data} />
        ) : (
          !policy.isError && <p className="text-sm text-muted-foreground">Loading…</p>
        )}
      </CardContent>
    </Card>
  );
}

export function PrivacyTab() {
  return (
    <div className="flex flex-col gap-6">
      <SubjectCard />
      <RetentionCard />
    </div>
  );
}
