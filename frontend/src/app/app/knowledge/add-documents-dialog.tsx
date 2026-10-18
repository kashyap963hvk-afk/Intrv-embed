"use client";

import { FileText, Link as LinkIcon, Plus, Upload, X } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";

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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { isOwner, useMe } from "@/lib/auth";
import { buildUrlSourceIn, connectorTabs, DEFAULT_INTERVAL_MINUTES } from "@/lib/connectors";
import { useConnectorKinds, useCreateSource } from "@/lib/connectors-api";
import { formatBytes, mergeFiles, type DocTypeChoice, type Space } from "@/lib/knowledge";
import { useAddUrl, usePasteDocument, useUploadDocuments, type UploadResult } from "@/lib/knowledge-api";
import { cn } from "@/lib/utils";

import { ConnectorIcon } from "./connector-icon";
import { ConnectorTab } from "./connector-tab";
import { DocTypeSelect } from "./doc-type-select";
import { SyncControls } from "./sync-controls";

const ACCEPTED_HINT =
  "PDF, Word, PowerPoint, Excel, HTML, Markdown, text, CSV, JSON/YAML, C/C++/Python/Rust code, images, or a .zip of them.";

function UploadResults({ result }: { result: UploadResult }) {
  return (
    <div className="flex flex-col gap-3 rounded-md border p-3 text-sm" aria-live="polite">
      {result.documents.length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="font-medium">Added ({result.documents.length})</p>
          <ul className="list-disc pl-5 text-muted-foreground">
            {result.documents.map((d) => (
              <li key={d.id}>{d.title}</li>
            ))}
          </ul>
        </div>
      )}
      {result.duplicates.length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="font-medium">Already in this space ({result.duplicates.length})</p>
          <ul className="list-disc pl-5 text-muted-foreground">
            {result.duplicates.map((d) => (
              <li key={d.id}>{d.title}</li>
            ))}
          </ul>
        </div>
      )}
      {result.rejected.length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="font-medium text-destructive">Rejected ({result.rejected.length})</p>
          <ul className="list-disc pl-5">
            {result.rejected.map((r, i) => (
              <li key={`${r.filename}-${i}`}>
                <span className="font-medium">{r.filename}</span>:{" "}
                <span className="text-muted-foreground">{r.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {result.documents.length + result.duplicates.length + result.rejected.length === 0 && (
        <p className="text-muted-foreground">The archive contained no files.</p>
      )}
    </div>
  );
}

function UploadTab({ spaceId }: { spaceId: string }) {
  const [files, setFiles] = useState<File[]>([]);
  const [docType, setDocType] = useState<DocTypeChoice>("AUTO");
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const upload = useUploadDocuments(spaceId);

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    setFiles((current) => mergeFiles(current, Array.from(e.dataTransfer.files)));
  }

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        upload.mutate({ files, docType }, { onSuccess: () => setFiles([]) });
      }}
    >
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          "flex flex-col items-center gap-2 rounded-lg border-2 border-dashed p-6 text-center text-sm transition-colors",
          dragging ? "border-ring bg-accent" : "border-input",
        )}
      >
        <Upload className="size-6 text-muted-foreground" aria-hidden />
        <p>Drag files or a .zip here, or</p>
        <Button type="button" variant="outline" size="sm" onClick={() => input.current?.click()}>
          Choose files
        </Button>
        <input
          ref={input}
          type="file"
          multiple
          className="sr-only"
          aria-label="Files to upload"
          tabIndex={-1}
          onChange={(e) => {
            const picked = Array.from(e.target.files ?? []);
            setFiles((current) => mergeFiles(current, picked));
            e.target.value = "";
          }}
        />
        <p className="text-xs text-muted-foreground">{ACCEPTED_HINT}</p>
      </div>

      {files.length > 0 && (
        <ul className="flex max-h-40 flex-col gap-1 overflow-y-auto text-sm">
          {files.map((file, index) => (
            <li
              key={`${file.name}-${file.size}-${file.lastModified}`}
              className="flex items-center justify-between gap-2"
            >
              <span className="truncate">{file.name}</span>
              <span className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
                {formatBytes(file.size)}
                <button
                  type="button"
                  aria-label={`Remove ${file.name}`}
                  className="rounded-xs hover:text-foreground"
                  onClick={() => setFiles((current) => current.filter((_, i) => i !== index))}
                >
                  <X className="size-4" />
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}

      <DocTypeSelect id="kn-upload-type" value={docType} onChange={setDocType} />
      {upload.data && <UploadResults result={upload.data} />}
      <DialogFooter>
        <Button type="submit" disabled={files.length === 0 || upload.isPending}>
          {upload.isPending
            ? "Uploading…"
            : `Upload ${files.length > 0 ? files.length : ""} ${files.length === 1 ? "file" : "files"}`}
        </Button>
      </DialogFooter>
    </form>
  );
}

function PasteTab({ spaceId, onDone }: { spaceId: string; onDone: () => void }) {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [docType, setDocType] = useState<DocTypeChoice>("AUTO");
  const paste = usePasteDocument(spaceId);
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        paste.mutate({ title: title.trim(), text, doc_type: docType }, { onSuccess: onDone });
      }}
    >
      <div className="flex flex-col gap-2">
        <Label htmlFor="kn-paste-title">Title</Label>
        <Input
          id="kn-paste-title"
          value={title}
          maxLength={300}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="e.g. Senior firmware engineer JD"
          required
        />
      </div>
      <div className="flex flex-col gap-2">
        <Label htmlFor="kn-paste-text">Text</Label>
        <Textarea
          id="kn-paste-text"
          rows={10}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Paste plain text or Markdown"
          required
        />
      </div>
      <DocTypeSelect id="kn-paste-type" value={docType} onChange={setDocType} />
      <DialogFooter>
        <Button type="submit" disabled={!title.trim() || !text.trim() || paste.isPending}>
          {paste.isPending ? "Adding…" : "Add text"}
        </Button>
      </DialogFooter>
    </form>
  );
}

function LinkTab({ spaceId, onDone }: { spaceId: string; onDone: () => void }) {
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [docType, setDocType] = useState<DocTypeChoice>("AUTO");
  const [keepInSync, setKeepInSync] = useState(false);
  const [intervalMinutes, setIntervalMinutes] = useState(DEFAULT_INTERVAL_MINUTES);
  const add = useAddUrl(spaceId);
  const createSource = useCreateSource(spaceId);
  const pending = add.isPending || createSource.isPending;
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (keepInSync) {
          // A URL source fetches the page now and again on schedule, replacing the indexed copy when it changes.
          createSource.mutate(buildUrlSourceIn({ url, title, docType, intervalMinutes }), { onSuccess: onDone });
        } else {
          add.mutate({ url: url.trim(), title: title.trim() || null, doc_type: docType }, { onSuccess: onDone });
        }
      }}
    >
      <div className="flex flex-col gap-2">
        <Label htmlFor="kn-url">Web page or PDF link</Label>
        <Input
          id="kn-url"
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://…"
          required
        />
        <p className="text-xs text-muted-foreground">
          {keepInSync ? "Fetched by the server now and on schedule." : "Fetched once by the server."} Only public
          addresses are allowed; files up to 25 MB.
        </p>
      </div>
      <div className="flex flex-col gap-2">
        <Label htmlFor="kn-url-title">Title (optional)</Label>
        <Input id="kn-url-title" value={title} maxLength={300} onChange={(e) => setTitle(e.target.value)} />
      </div>
      <DocTypeSelect id="kn-url-type" value={docType} onChange={setDocType} />
      <SyncControls
        idPrefix="kn-url"
        enabled={keepInSync}
        onEnabledChange={setKeepInSync}
        intervalMinutes={intervalMinutes}
        onIntervalChange={setIntervalMinutes}
        help="Checks the page again on a schedule and re-indexes it when it changes."
      />
      <DialogFooter>
        <Button type="submit" disabled={url.trim().length < 8 || pending}>
          {pending ? "Adding…" : keepInSync ? "Add and keep in sync" : "Add link"}
        </Button>
      </DialogFooter>
    </form>
  );
}

/** The "+ Add" button of a space: upload files, paste text, add a link, or import from connected storage. */
export function AddDocumentsDialog({ space }: { space: Space }) {
  // The dialog content unmounts when closed, so every open starts with empty forms.
  const [open, setOpen] = useState(false);
  // While the Google Picker is up, the dialog stays mounted but closed (hidden, with its focus trap released) so the
  // picker's frame can take focus and clicks; it reopens with the selection when the picker closes.
  const [pickerOpen, setPickerOpen] = useState(false);
  const me = useMe();
  const owner = isOwner(me.data);
  const kinds = useConnectorKinds(open);
  const tabs = connectorTabs(kinds.data ?? [], owner);
  const close = () => setOpen(false);

  return (
    <Dialog
      open={open && !pickerOpen}
      onOpenChange={(next) => {
        if (!pickerOpen) setOpen(next);
      }}
    >
      <DialogTrigger asChild>
        <Button>
          <Plus /> Add
        </Button>
      </DialogTrigger>
      <DialogContent forceMount={open ? true : undefined} className={cn("sm:max-w-3xl", pickerOpen && "invisible")}>
        <DialogHeader>
          <DialogTitle>Add to {space.name}</DialogTitle>
          <DialogDescription>
            Documents are parsed, checked for hidden text and injected instructions, then indexed for search.
          </DialogDescription>
        </DialogHeader>
        <Tabs defaultValue="upload">
          <TabsList className="h-auto flex-wrap justify-start">
            <TabsTrigger value="upload" className="h-8 flex-none">
              <Upload /> Upload
            </TabsTrigger>
            <TabsTrigger value="paste" className="h-8 flex-none">
              <FileText /> Paste
            </TabsTrigger>
            <TabsTrigger value="link" className="h-8 flex-none">
              <LinkIcon /> Link
            </TabsTrigger>
            {tabs.map(({ info, tab }) => (
              <TabsTrigger key={info.kind} value={info.kind} className="h-8 flex-none">
                <ConnectorIcon kind={info.kind} /> {tab}
              </TabsTrigger>
            ))}
          </TabsList>
          <TabsContent value="upload">
            <UploadTab spaceId={space.id} />
          </TabsContent>
          <TabsContent value="paste">
            <PasteTab spaceId={space.id} onDone={close} />
          </TabsContent>
          <TabsContent value="link">
            <LinkTab spaceId={space.id} onDone={close} />
          </TabsContent>
          {tabs.map(({ info }) => (
            <TabsContent key={info.kind} value={info.kind}>
              <ConnectorTab info={info} space={space} owner={owner} onDone={close} onPickerOpenChange={setPickerOpen} />
            </TabsContent>
          ))}
        </Tabs>
        {kinds.isError && (
          <p className="text-xs text-destructive">Cloud storage options could not be loaded: {kinds.error.message}</p>
        )}
      </DialogContent>
    </Dialog>
  );
}
