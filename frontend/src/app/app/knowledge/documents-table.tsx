"use client";

import { Download, LoaderCircle, RefreshCw, ShieldAlert, Trash } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  docTypeLabel,
  formatBytes,
  isInProgress,
  ocrPages,
  riskFlags,
  type DocStatus,
  type KnowledgeDocument,
  type RiskFlag,
  type Space,
} from "@/lib/knowledge";
import { useDeleteDocument, useDocuments, useDownloadDocument, useReindexDocument } from "@/lib/knowledge-api";

const STATUS = {
  PENDING: { label: "Queued", variant: "outline" },
  PARSING: { label: "Parsing", variant: "secondary" },
  EMBEDDING: { label: "Embedding", variant: "secondary" },
  READY: { label: "Ready", variant: "success" },
  FAILED: { label: "Failed", variant: "destructive" },
  DELETING: { label: "Deleting", variant: "outline" },
} as const satisfies Record<DocStatus, { label: string; variant: string }>;

const MAX_SNIPPET = 240;

export function StatusBadge({ status }: { status: DocStatus }) {
  const { label, variant } = STATUS[status];
  return (
    <Badge variant={variant}>
      {isInProgress(status) && <LoaderCircle className="animate-spin" aria-hidden />}
      {label}
    </Badge>
  );
}

function RiskBadge({ risk }: { risk: RiskFlag }) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="rounded-md outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
          aria-label={`${risk.label}: show details`}
        >
          <Badge variant="destructive">
            <ShieldAlert aria-hidden />
            {risk.label}
          </Badge>
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-96 max-w-[90vw]" align="start">
        <div className="flex flex-col gap-2 text-sm">
          <p className="font-medium">{risk.label}</p>
          <p className="text-xs text-muted-foreground">{risk.help}</p>
          <ul className="flex max-h-60 flex-col gap-1 overflow-y-auto">
            {risk.items.map((item, i) => (
              <li key={i} className="rounded bg-muted px-2 py-1 font-mono text-xs break-words whitespace-pre-wrap">
                {item.length > MAX_SNIPPET ? `${item.slice(0, MAX_SNIPPET)}…` : item}
              </li>
            ))}
          </ul>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function DocumentRow({ doc, canWrite }: { doc: KnowledgeDocument; canWrite: boolean }) {
  const download = useDownloadDocument();
  const reindex = useReindexDocument();
  const remove = useDeleteDocument();
  const risks = riskFlags(doc.risk_flags);
  const ocr = ocrPages(doc.risk_flags);
  const busy = isInProgress(doc.status);
  const source = doc.source_url ?? doc.filename;

  return (
    <TableRow>
      <TableCell className="max-w-xs">
        <div className="flex flex-col gap-0.5">
          <span className="truncate font-medium" title={doc.title}>
            {doc.title}
          </span>
          {source && (
            <span className="truncate text-xs text-muted-foreground" title={source}>
              {source}
              {doc.size_bytes > 0 && ` · ${formatBytes(doc.size_bytes)}`}
            </span>
          )}
          {doc.status === "FAILED" && doc.error && <span className="text-xs text-destructive">{doc.error}</span>}
        </div>
      </TableCell>
      <TableCell className="whitespace-nowrap">{docTypeLabel(doc.doc_type)}</TableCell>
      <TableCell>
        <StatusBadge status={doc.status} />
      </TableCell>
      <TableCell className="text-xs whitespace-nowrap text-muted-foreground">
        {doc.page_count != null ? plural(doc.page_count, "page") : "—"} · {plural(doc.chunk_count, "chunk")}
      </TableCell>
      <TableCell>
        <div className="flex flex-wrap gap-1">
          {risks.map((risk) => (
            <RiskBadge key={risk.key} risk={risk} />
          ))}
          {ocr.length > 0 && (
            <Badge variant="outline" title={`Text recognised by OCR on page ${ocr.join(", ")}`}>
              OCR
            </Badge>
          )}
        </div>
      </TableCell>
      <TableCell>
        <div className="flex justify-end gap-1">
          <Button
            size="icon"
            variant="ghost"
            aria-label={`Download ${doc.title}`}
            title="Download original"
            disabled={download.isPending || doc.status === "DELETING"}
            onClick={() => download.mutate(doc)}
          >
            <Download />
          </Button>
          {canWrite && (
            <>
              <Button
                size="icon"
                variant="ghost"
                aria-label={`Re-index ${doc.title}`}
                title="Parse and index again"
                disabled={busy || reindex.isPending}
                onClick={() => reindex.mutate(doc)}
              >
                <RefreshCw />
              </Button>
              <Button
                size="icon"
                variant="ghost"
                className="text-destructive"
                aria-label={`Delete ${doc.title}`}
                title="Delete"
                disabled={doc.status === "DELETING" || remove.isPending}
                onClick={() =>
                  window.confirm(`Delete ${doc.title}? Its file and search index entries are removed for everyone.`) &&
                  remove.mutate(doc)
                }
              >
                <Trash />
              </Button>
            </>
          )}
        </div>
      </TableCell>
    </TableRow>
  );
}

export function DocumentsTable({ space }: { space: Space }) {
  const documents = useDocuments(space.id);
  const canWrite = space.permission === "CONTRIBUTE";

  if (documents.isPending) return <p className="text-sm text-muted-foreground">Loading documents…</p>;
  if (documents.isError) return <p className="text-sm text-destructive">{documents.error.message}</p>;
  if (documents.data.length === 0) {
    return (
      <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
        {canWrite
          ? "No documents yet. Use Add to upload files, paste text or add a link."
          : "No documents in this space yet."}
      </p>
    );
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Title</TableHead>
          <TableHead>Type</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Size</TableHead>
          <TableHead>Risks</TableHead>
          <TableHead className="text-right">
            <span className="sr-only">Actions</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {documents.data.map((doc) => (
          <DocumentRow key={doc.id} doc={doc} canWrite={canWrite} />
        ))}
      </TableBody>
    </Table>
  );
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}
