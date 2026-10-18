"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { toast } from "sonner";

import { api, type Schemas } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import { env } from "@/lib/env";
import {
  applyStatusEvent,
  buildUploadForm,
  isInProgress,
  needsRefetch,
  parseDocumentStatusEvent,
  uploadSummary,
  type DocTypeChoice,
  type GrantIn,
  type KnowledgeDocument,
} from "@/lib/knowledge";

export type SearchIn = Schemas["SearchIn"];
export type SearchOut = Schemas["SearchOut"];
export type SearchHit = Schemas["SearchHit"];
export type UploadResult = Schemas["UploadResult"];
export type SelfTest = Schemas["SelfTestOut"];

export const knowledgeKeys = {
  all: ["knowledge"] as const,
  spaces: ["knowledge", "spaces"] as const,
  documents: (spaceId: string) => ["knowledge", "documents", spaceId] as const,
  /** Under "admin" so the admin console's mutations refresh it too. */
  orgSpaces: ["admin", "spaces"] as const,
};

export function useSpaces() {
  return useQuery({ queryKey: knowledgeKeys.spaces, queryFn: async () => unwrap(await api.GET("/api/spaces")) });
}

export function useDocuments(spaceId: string | null) {
  return useQuery({
    queryKey: knowledgeKeys.documents(spaceId ?? ""),
    queryFn: async () =>
      unwrap(await api.GET("/api/spaces/{space_id}/documents", { params: { path: { space_id: spaceId ?? "" } } })),
    enabled: spaceId !== null,
    // Live updates arrive over SSE; this slow poll only covers a dropped event stream while work is in flight.
    refetchInterval: (query) => (query.state.data?.some((d) => isInProgress(d.status)) ? 15_000 : false),
  });
}

/** Wraps a knowledge mutation with toast feedback and knowledge cache invalidation. */
export function useKnowledgeMutation<TVars, TResult>(
  fn: (vars: TVars) => Promise<TResult>,
  success?: string | ((result: TResult) => string),
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (result) => {
      if (success) toast.success(typeof success === "function" ? success(result) : success);
      void queryClient.invalidateQueries({ queryKey: knowledgeKeys.all });
    },
    onError: (error) => toast.error(error.message),
  });
}

export function useCreateSpace() {
  return useKnowledgeMutation(
    async (body: { name: string; description: string }) => unwrap(await api.POST("/api/spaces", { body })),
    (space) => `Space ${space.name} created`,
  );
}

export function useUploadDocuments(spaceId: string) {
  return useKnowledgeMutation(
    async ({ files, docType }: { files: File[]; docType: DocTypeChoice }) =>
      unwrap(
        await api.POST("/api/spaces/{space_id}/documents", {
          params: { path: { space_id: spaceId } },
          // The typed body documents the fields; the serializer sends the real files as multipart/form-data.
          body: { files: files.map((f) => f.name), doc_type: docType },
          bodySerializer: () => buildUploadForm(files, docType),
        }),
      ),
    uploadSummary,
  );
}

export function usePasteDocument(spaceId: string) {
  return useKnowledgeMutation(
    async (body: { title: string; text: string; doc_type: DocTypeChoice }) =>
      unwrap(
        await api.POST("/api/spaces/{space_id}/documents/paste", { params: { path: { space_id: spaceId } }, body }),
      ),
    (doc) => `${doc.title} added`,
  );
}

export function useAddUrl(spaceId: string) {
  return useKnowledgeMutation(
    async (body: { url: string; title: string | null; doc_type: DocTypeChoice }) =>
      unwrap(await api.POST("/api/spaces/{space_id}/documents/url", { params: { path: { space_id: spaceId } }, body })),
    "Link added. Fetching it now…",
  );
}

export function useReindexDocument() {
  return useKnowledgeMutation(
    async (doc: KnowledgeDocument) =>
      unwrap(await api.POST("/api/documents/{document_id}/reindex", { params: { path: { document_id: doc.id } } })),
    (doc) => `Re-indexing ${doc.title}`,
  );
}

export function useDeleteDocument() {
  return useKnowledgeMutation(
    async (doc: KnowledgeDocument) => {
      unwrap(await api.DELETE("/api/documents/{document_id}", { params: { path: { document_id: doc.id } } }));
      return doc;
    },
    (doc) => `${doc.title} deleted`,
  );
}

/**
 * Download the original file through a short-lived pre-signed URL. The tab is opened synchronously (inside the
 * click) so popup blockers allow it, then pointed at the URL once the API has issued it.
 */
export function useDownloadDocument() {
  return useMutation({
    mutationFn: async (doc: KnowledgeDocument) => {
      const tab = window.open("", "_blank");
      try {
        const { url } = unwrap(
          await api.GET("/api/documents/{document_id}/download", { params: { path: { document_id: doc.id } } }),
        );
        if (tab) {
          tab.opener = null;
          tab.location.href = url;
        } else {
          window.location.assign(url);
        }
      } catch (error) {
        tab?.close();
        throw error;
      }
    },
    onError: (error) => toast.error(error.message),
  });
}

export function useSearch() {
  return useMutation({
    mutationFn: async (body: SearchIn) => unwrap(await api.POST("/api/search", { body })),
    onError: (error) => toast.error(error.message),
  });
}

// --- owner: organization spaces --------------------------------------------------------------------------------

export function useOrgSpaces() {
  return useQuery({
    queryKey: knowledgeKeys.orgSpaces,
    queryFn: async () => unwrap(await api.GET("/api/admin/spaces")),
  });
}

/** Org space changes affect both the admin list and everyone's space list. */
export function useSpaceAdminMutation<TVars, TResult>(
  fn: (vars: TVars) => Promise<TResult>,
  success: string | ((result: TResult) => string),
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (result) => {
      toast.success(typeof success === "function" ? success(result) : success);
      void queryClient.invalidateQueries({ queryKey: knowledgeKeys.orgSpaces });
      void queryClient.invalidateQueries({ queryKey: knowledgeKeys.all });
    },
    onError: (error) => toast.error(error.message),
  });
}

export function useCreateOrgSpace() {
  return useSpaceAdminMutation(
    async (body: { name: string; description: string }) => unwrap(await api.POST("/api/admin/spaces", { body })),
    (space) => `Space ${space.name} created`,
  );
}

export function useUpdateOrgSpace(spaceId: string) {
  return useSpaceAdminMutation(
    async (body: { name: string; description: string }) =>
      unwrap(await api.PATCH("/api/admin/spaces/{space_id}", { params: { path: { space_id: spaceId } }, body })),
    (space) => `Space ${space.name} saved`,
  );
}

export function useDeleteOrgSpace(spaceId: string, name: string) {
  return useSpaceAdminMutation(
    async () => unwrap(await api.DELETE("/api/admin/spaces/{space_id}", { params: { path: { space_id: spaceId } } })),
    `Space ${name} deleted`,
  );
}

export function useSetGrants(spaceId: string) {
  return useSpaceAdminMutation(
    async (grants: GrantIn[]) =>
      unwrap(
        await api.PUT("/api/admin/spaces/{space_id}/grants", { params: { path: { space_id: spaceId } }, body: grants }),
      ),
    (space) => `Access to ${space.name} saved`,
  );
}

export function useSelfTest() {
  return useMutation({
    mutationFn: async () => unwrap(await api.GET("/api/system/selftest")),
    onError: (error) => toast.error(error.message),
  });
}

// --- live document status --------------------------------------------------------------------------------------

const RECONNECT_DELAYS_MS = [2_000, 5_000, 15_000, 30_000];

/**
 * Subscribe to `/api/events` while mounted and fold `document.status` events into the documents cache of the
 * event's space. The server only sends events for spaces this user can read. When the stream fails for good
 * (for example an expired session), the session is refreshed through the API client and the stream reopened,
 * and documents are refetched to pick up anything missed meanwhile.
 */
export function useDocumentEvents() {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let failures = 0;
    let closed = false;

    const onStatus = (message: MessageEvent<string>) => {
      const event = parseDocumentStatusEvent(message.data);
      if (!event) return;
      const key = knowledgeKeys.documents(event.space_id);
      const cached = queryClient.getQueryData<KnowledgeDocument[]>(key);
      queryClient.setQueryData<KnowledgeDocument[]>(key, (docs) => applyStatusEvent(docs, event));
      if (needsRefetch(cached, event)) {
        void queryClient.invalidateQueries({ queryKey: key });
        void queryClient.invalidateQueries({ queryKey: knowledgeKeys.spaces });
      }
    };

    const connect = () => {
      source = new EventSource(`${env.apiBaseUrl}/api/events`, { withCredentials: true });
      source.addEventListener("document.status", onStatus);
      source.onopen = () => {
        if (failures > 0) void queryClient.invalidateQueries({ queryKey: ["knowledge", "documents"] });
        failures = 0;
      };
      source.onerror = () => {
        // The browser retries transient drops itself; CLOSED means it gave up (e.g. HTTP 401).
        if (!source || source.readyState !== EventSource.CLOSED || closed) return;
        source.close();
        const delay = RECONNECT_DELAYS_MS[Math.min(failures, RECONNECT_DELAYS_MS.length - 1)];
        failures += 1;
        timer = setTimeout(() => {
          // Any authenticated call refreshes an expired session before the stream reconnects.
          void api.GET("/api/auth/me").finally(() => {
            if (!closed) connect();
          });
        }, delay);
      };
    };

    connect();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      source?.close();
    };
  }, [queryClient]);
}
