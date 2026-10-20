"use client";

import { useMutation, useQueries, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import { cvKeys } from "@/lib/cv-api";
import { filenameFromDisposition, saveBlob } from "@/lib/download";
import { useEventStream, type EventStreamHandlers } from "@/lib/event-stream";
import type { Space } from "@/lib/knowledge";
import { knowledgeKeys } from "@/lib/knowledge-api";
import {
  applyOrder,
  applySetEventToDetail,
  applySetEventToList,
  exportFilename,
  isGenerating,
  parseSetStatusEvent,
  removeItem,
  replaceItem,
  setEventNeedsRefetch,
  type CreateSetIn,
  type ExportFormat,
  type ItemUpdate,
  type QuestionItem,
  type QuestionSet,
  type QuestionSetDetail,
} from "@/lib/questions";

export const questionKeys = {
  all: ["questions"] as const,
  sets: ["questions", "sets"] as const,
  set: (setId: string) => ["questions", "set", setId] as const,
};

/** Fallback poll while a set is generating, in case the event stream drops an update. */
const GENERATING_POLL_MS = 3_000;

export function useQuestionSets() {
  return useQuery({
    queryKey: questionKeys.sets,
    queryFn: async () => unwrap(await api.GET("/api/question-sets")),
    refetchInterval: (query) => (query.state.data?.some(isGenerating) ? GENERATING_POLL_MS : false),
  });
}

export function useQuestionSet(setId: string) {
  return useQuery({
    queryKey: questionKeys.set(setId),
    queryFn: async () => unwrap(await api.GET("/api/question-sets/{set_id}", { params: { path: { set_id: setId } } })),
    refetchInterval: (query) => (isGenerating(query.state.data) ? GENERATING_POLL_MS : false),
    retry: false,
  });
}

/** The documents of every space, for the CV / JD pickers; shares the knowledge page's cache. */
export function useSpaceDocuments(spaces: readonly Space[]) {
  return useQueries({
    queries: spaces.map((space) => ({
      queryKey: knowledgeKeys.documents(space.id),
      queryFn: async () =>
        unwrap(await api.GET("/api/spaces/{space_id}/documents", { params: { path: { space_id: space.id } } })),
    })),
  });
}

function onError(error: Error) {
  toast.error(error.message);
}

export function useCreateSet() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: CreateSetIn) => unwrap(await api.POST("/api/question-sets", { body })),
    onSuccess: (set) => {
      queryClient.setQueryData(questionKeys.set(set.id), set);
      void queryClient.invalidateQueries({ queryKey: questionKeys.sets });
    },
    onError,
  });
}

/** Mutations that return the whole set: the detail cache is replaced and the list refreshed. */
function useSetMutation<TVars>(setId: string, fn: (vars: TVars) => Promise<QuestionSetDetail>, success?: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (set) => {
      if (success) toast.success(success);
      queryClient.setQueryData(questionKeys.set(setId), set);
      void queryClient.invalidateQueries({ queryKey: questionKeys.sets });
    },
    onError,
  });
}

export function useRenameSet(setId: string) {
  return useSetMutation(
    setId,
    async (title: string) =>
      unwrap(await api.PATCH("/api/question-sets/{set_id}", { params: { path: { set_id: setId } }, body: { title } })),
    "Title saved",
  );
}

export function useRetrySet(setId: string) {
  return useSetMutation(setId, async () =>
    unwrap(await api.POST("/api/question-sets/{set_id}/retry", { params: { path: { set_id: setId } } })),
  );
}

export function useDeleteSet() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (set: Pick<QuestionSet, "id" | "title">) => {
      unwrap(await api.DELETE("/api/question-sets/{set_id}", { params: { path: { set_id: set.id } } }));
      return set;
    },
    onSuccess: (set) => {
      toast.success(`${set.title} deleted`);
      queryClient.removeQueries({ queryKey: questionKeys.set(set.id) });
      void queryClient.invalidateQueries({ queryKey: questionKeys.sets });
    },
    onError,
  });
}

/** Reorder optimistically; the server's order wins once it answers (or the old order comes back on failure). */
export function useReorderItems(setId: string) {
  const queryClient = useQueryClient();
  const key = questionKeys.set(setId);
  return useMutation({
    mutationFn: async (itemIds: string[]) =>
      unwrap(
        await api.POST("/api/question-sets/{set_id}/items/reorder", {
          params: { path: { set_id: setId } },
          body: { item_ids: itemIds },
        }),
      ),
    onMutate: async (itemIds) => {
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<QuestionSetDetail>(key);
      if (previous) queryClient.setQueryData(key, applyOrder(previous, itemIds));
      return { previous };
    },
    onSuccess: (set) => queryClient.setQueryData(key, set),
    onError: (error, _ids, context) => {
      if (context?.previous) queryClient.setQueryData(key, context.previous);
      onError(error);
    },
  });
}

/** Item mutations return the item: it replaces the cached one. */
function useItemMutation<TVars>(
  setId: string,
  fn: (vars: TVars) => Promise<QuestionItem>,
  success?: string | ((vars: TVars) => string | null),
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (item, vars) => {
      const message = typeof success === "function" ? success(vars) : success;
      if (message) toast.success(message);
      queryClient.setQueryData<QuestionSetDetail>(questionKeys.set(setId), (set) => set && replaceItem(set, item));
    },
    onError,
  });
}

export function useUpdateItem(setId: string) {
  return useItemMutation(
    setId,
    async ({ itemId, update }: { itemId: string; update: ItemUpdate }) =>
      unwrap(
        await api.PATCH("/api/question-sets/{set_id}/items/{item_id}", {
          params: { path: { set_id: setId, item_id: itemId } },
          body: update,
        }),
      ),
    // Pinning shows on the row itself; edits get a confirmation.
    ({ update }) => (Object.keys(update).some((k) => k !== "pinned") ? "Question saved" : null),
  );
}

export function useRegenerateItem(setId: string) {
  return useItemMutation(
    setId,
    async ({ itemId, hint }: { itemId: string; hint: string | null }) =>
      unwrap(
        await api.POST("/api/question-sets/{set_id}/items/{item_id}/regenerate", {
          params: { path: { set_id: setId, item_id: itemId } },
          body: { hint },
        }),
      ),
    "Question regenerated",
  );
}

export function useDeleteItem(setId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (itemId: string) => {
      unwrap(
        await api.DELETE("/api/question-sets/{set_id}/items/{item_id}", {
          params: { path: { set_id: setId, item_id: itemId } },
        }),
      );
      return itemId;
    },
    onSuccess: (itemId) => {
      toast.success("Question removed");
      queryClient.setQueryData<QuestionSetDetail>(questionKeys.set(setId), (set) => set && removeItem(set, itemId));
      void queryClient.invalidateQueries({ queryKey: questionKeys.sets });
    },
    onError,
  });
}

/** Download the interviewer copy (with model answers) as Markdown, PDF or Word. */
export function useExportSet() {
  return useMutation({
    mutationFn: async ({ set, format }: { set: Pick<QuestionSet, "id" | "title">; format: ExportFormat }) => {
      const result = await api.GET("/api/question-sets/{set_id}/export", {
        params: { path: { set_id: set.id }, query: { format } },
        parseAs: "blob",
      });
      const blob = unwrap(result);
      const fallback = exportFilename(set.title, format);
      saveBlob(blob, filenameFromDisposition(result.response.headers.get("Content-Disposition"), fallback));
    },
    onError,
  });
}

// --- live generation progress --------------------------------------------------------------------------------------

/**
 * Fold `question_set.status` events (sent only to the set's owner) into the list and detail caches. When a set
 * finishes, its items, analysis and the CV profile it created are refetched.
 */
function questionSetEventHandlers(queryClient: QueryClient): EventStreamHandlers {
  return {
    listeners: {
      "question_set.status": (data) => {
        const event = parseSetStatusEvent(data);
        if (!event) return;
        const cached = queryClient.getQueryData<QuestionSet[]>(questionKeys.sets);
        queryClient.setQueryData<QuestionSet[]>(questionKeys.sets, (sets) => applySetEventToList(sets, event));
        queryClient.setQueryData<QuestionSetDetail>(questionKeys.set(event.set_id), (set) =>
          applySetEventToDetail(set, event),
        );
        if (setEventNeedsRefetch(cached, event)) {
          void queryClient.invalidateQueries({ queryKey: questionKeys.sets });
          if (event.status !== "GENERATING") {
            void queryClient.invalidateQueries({ queryKey: questionKeys.set(event.set_id) });
            void queryClient.invalidateQueries({ queryKey: cvKeys.all });
          }
        }
      },
    },
    resync: () => {
      void queryClient.invalidateQueries({ queryKey: questionKeys.all });
    },
  };
}

/** Live generation progress over `/api/events` while mounted (polling covers a dropped stream). */
export function useQuestionSetEvents() {
  useEventStream(questionSetEventHandlers);
}
