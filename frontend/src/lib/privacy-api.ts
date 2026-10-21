"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import { filenameFromDisposition, saveBlob } from "@/lib/download";
import { buildEraseIn, countSummary, EXPORT_FILENAME, type SubjectIn } from "@/lib/privacy";

function onError(error: Error) {
  toast.error(error.message);
}

/** Preview everything held about a candidate (owners only; nothing changes). */
export function useSubjectSearch() {
  return useMutation({
    mutationFn: async (body: SubjectIn) => unwrap(await api.POST("/api/privacy/search", { body })),
    onError,
  });
}

/** Subject access export: download everything held, decrypted, as one JSON file. The export is audited. */
export function useSubjectExport() {
  return useMutation({
    mutationFn: async (body: SubjectIn) => {
      const result = await api.POST("/api/privacy/export", { body, parseAs: "blob" });
      const blob = unwrap(result);
      saveBlob(blob, filenameFromDisposition(result.response.headers.get("Content-Disposition"), EXPORT_FILENAME));
    },
    onSuccess: () => toast.success("Export downloaded. It contains personal data: store and send it securely."),
    onError,
  });
}

/** Erase everything that matches the subject, everywhere. Cannot be undone. */
export function useSubjectErase() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ subject, reason }: { subject: SubjectIn; reason: string }) =>
      unwrap(await api.POST("/api/privacy/erase", { body: buildEraseIn(subject, reason) })),
    onSuccess: (result) => {
      toast.success(`Erased ${countSummary(result.erased)}.`);
      // Interviews, CVs, question sets and the audit log all changed.
      void queryClient.invalidateQueries();
    },
    onError,
  });
}
