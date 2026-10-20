"use client";

import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import { filenameFromDisposition, saveBlob } from "@/lib/download";
import { useEventStream, type EventStreamHandlers } from "@/lib/event-stream";
import {
  applyStatusEventToDetail,
  applyStatusEventToList,
  gradingInProgress,
  isPreparing,
  parseJoinInfo,
  parseReport,
  parseReportEvent,
  parseStatusEvent,
  planPatch,
  reportFilename,
  statusEventNeedsRefetch,
  type BrowserEvent,
  type CreateInterviewIn,
  type Decision,
  type Interview,
  type InterviewDetail,
  type PlanTopic,
  type Report,
  type ReportFormat,
  type Template,
  type TemplateIn,
  type TemplatePatch,
} from "@/lib/interviews";

export const interviewKeys = {
  all: ["interviews"] as const,
  list: ["interviews", "list"] as const,
  detail: (id: string) => ["interviews", "detail", id] as const,
  report: (id: string) => ["interviews", "report", id] as const,
  templates: ["interviews", "templates"] as const,
  template: (id: string) => ["interviews", "template", id] as const,
  join: (token: string) => ["join", token] as const,
};

/** Fallback polls in case the event stream drops an update. */
const PREPARING_POLL_MS = 3_000;
const GRADING_POLL_MS = 5_000;

function onError(error: Error) {
  toast.error(error.message);
}

// --- interviews --------------------------------------------------------------------------------------------------

export function useInterviews() {
  return useQuery({
    queryKey: interviewKeys.list,
    queryFn: async () => unwrap(await api.GET("/api/interviews")),
    refetchInterval: (query) =>
      query.state.data?.some((i) => isPreparing(i) || i.scorecard_status === "PENDING") ? PREPARING_POLL_MS : false,
  });
}

export function useInterview(id: string) {
  return useQuery({
    queryKey: interviewKeys.detail(id),
    queryFn: async () =>
      unwrap(await api.GET("/api/interviews/{session_id}", { params: { path: { session_id: id } } })),
    refetchInterval: (query) => (isPreparing(query.state.data) ? PREPARING_POLL_MS : false),
    retry: false,
  });
}

function cacheDetail(queryClient: QueryClient, detail: InterviewDetail) {
  queryClient.setQueryData(interviewKeys.detail(detail.id), detail);
  void queryClient.invalidateQueries({ queryKey: interviewKeys.list });
}

export function useCreateInterview() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: CreateInterviewIn) => unwrap(await api.POST("/api/interviews", { body })),
    onSuccess: (detail) => cacheDetail(queryClient, detail),
    onError,
  });
}

/** Mutations that return the whole interview: the detail cache is replaced and the list refreshed. */
function useDetailMutation<TVars>(fn: (vars: TVars) => Promise<InterviewDetail>, success?: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (detail) => {
      if (success) toast.success(success);
      cacheDetail(queryClient, detail);
    },
    onError,
  });
}

export function useUpdatePlan(id: string) {
  return useDetailMutation(async (topics: PlanTopic[]) =>
    unwrap(
      await api.PATCH("/api/interviews/{session_id}/plan", {
        params: { path: { session_id: id } },
        body: planPatch(topics),
      }),
    ),
  );
}

export function usePrepareAgain(id: string) {
  return useDetailMutation(
    async () =>
      unwrap(await api.POST("/api/interviews/{session_id}/prepare", { params: { path: { session_id: id } } })),
    "Preparing the interview again",
  );
}

export function useEndInterview(id: string) {
  const queryClient = useQueryClient();
  return useDetailMutation(async () => {
    const detail = unwrap(await api.POST("/api/interviews/{session_id}/end", { params: { path: { session_id: id } } }));
    void queryClient.invalidateQueries({ queryKey: interviewKeys.report(id) });
    return detail;
  });
}

/** Create (or rotate) the candidate link. The URL is only ever returned here: the server keeps a hash. */
export function useInvite(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (hours: number) =>
      unwrap(
        await api.POST("/api/interviews/{session_id}/invite", {
          params: { path: { session_id: id } },
          body: { expires_in_hours: hours },
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: interviewKeys.detail(id) });
      void queryClient.invalidateQueries({ queryKey: interviewKeys.list });
    },
    onError,
  });
}

export function useDeleteInterview() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (interview: Pick<Interview, "id" | "title">) => {
      unwrap(await api.DELETE("/api/interviews/{session_id}", { params: { path: { session_id: interview.id } } }));
      return interview;
    },
    onSuccess: (interview) => {
      toast.success(`${interview.title} deleted`);
      queryClient.removeQueries({ queryKey: interviewKeys.detail(interview.id) });
      queryClient.removeQueries({ queryKey: interviewKeys.report(interview.id) });
      void queryClient.invalidateQueries({ queryKey: interviewKeys.list });
    },
    onError,
  });
}

// --- report ------------------------------------------------------------------------------------------------------

export function useReport(id: string) {
  return useQuery({
    queryKey: interviewKeys.report(id),
    queryFn: async (): Promise<Report> =>
      parseReport(
        unwrap(await api.GET("/api/interviews/{session_id}/report", { params: { path: { session_id: id } } })),
      ),
    refetchInterval: (query) =>
      gradingInProgress(query.state.data) || query.state.data?.status === "LIVE" ? GRADING_POLL_MS : false,
    retry: false,
  });
}

export function useRegrade(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      unwrap(await api.POST("/api/interviews/{session_id}/regrade", { params: { path: { session_id: id } } }));
    },
    onSuccess: () => {
      toast.success("Grading again. This page updates when the report is ready.");
      queryClient.setQueryData<Report>(interviewKeys.report(id), (report) =>
        report?.scorecard ? { ...report, scorecard: { ...report.scorecard, status: "PENDING", error: null } } : report,
      );
      void queryClient.invalidateQueries({ queryKey: interviewKeys.report(id) });
      void queryClient.invalidateQueries({ queryKey: interviewKeys.list });
    },
    onError,
  });
}

export function useRecordDecision(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: { decision: Decision; notes: string }) =>
      unwrap(await api.POST("/api/interviews/{session_id}/decision", { params: { path: { session_id: id } }, body })),
    onSuccess: () => {
      toast.success("Decision saved");
      void queryClient.invalidateQueries({ queryKey: interviewKeys.report(id) });
      void queryClient.invalidateQueries({ queryKey: interviewKeys.list });
    },
    onError,
  });
}

/** Download the report as PDF or JSON. */
export function useExportReport() {
  return useMutation({
    mutationFn: async ({ id, title, format }: { id: string; title: string; format: ReportFormat }) => {
      const result = await api.GET("/api/interviews/{session_id}/export", {
        params: { path: { session_id: id }, query: { format } },
        parseAs: "blob",
      });
      const blob = unwrap(result);
      const fallback = reportFilename(title, format);
      saveBlob(blob, filenameFromDisposition(result.response.headers.get("Content-Disposition"), fallback));
    },
    onError,
  });
}

// --- live progress -----------------------------------------------------------------------------------------------

/**
 * Fold `interview.status` events (preparation progress) into the list and detail caches, and refetch the report
 * when `interview.report` says grading finished. Events are only sent to the interview's creator.
 */
function interviewEventHandlers(queryClient: QueryClient): EventStreamHandlers {
  return {
    listeners: {
      "interview.status": (data) => {
        const event = parseStatusEvent(data);
        if (!event) return;
        const cached = queryClient.getQueryData<Interview[]>(interviewKeys.list);
        queryClient.setQueryData<Interview[]>(interviewKeys.list, (list) => applyStatusEventToList(list, event));
        queryClient.setQueryData<InterviewDetail>(interviewKeys.detail(event.session_id), (detail) =>
          applyStatusEventToDetail(detail, event),
        );
        if (statusEventNeedsRefetch(cached, event)) {
          void queryClient.invalidateQueries({ queryKey: interviewKeys.list });
          if (event.status !== "PREPARING") {
            void queryClient.invalidateQueries({ queryKey: interviewKeys.detail(event.session_id) });
          }
        }
      },
      "interview.report": (data) => {
        const event = parseReportEvent(data);
        if (!event) return;
        void queryClient.invalidateQueries({ queryKey: interviewKeys.report(event.session_id) });
        void queryClient.invalidateQueries({ queryKey: interviewKeys.list });
      },
    },
    resync: () => {
      void queryClient.invalidateQueries({ queryKey: interviewKeys.all });
    },
  };
}

/** Live preparation and grading progress over `/api/events` while mounted (polling covers a dropped stream). */
export function useInterviewEvents() {
  useEventStream(interviewEventHandlers);
}

// --- templates ---------------------------------------------------------------------------------------------------

export function useTemplates() {
  return useQuery({
    queryKey: interviewKeys.templates,
    queryFn: async () => unwrap(await api.GET("/api/templates")),
  });
}

export function useTemplate(id: string) {
  return useQuery({
    queryKey: interviewKeys.template(id),
    queryFn: async () =>
      unwrap(await api.GET("/api/templates/{template_id}", { params: { path: { template_id: id } } })),
    retry: false,
  });
}

function cacheTemplate(queryClient: QueryClient, template: Template) {
  queryClient.setQueryData(interviewKeys.template(template.id), template);
  void queryClient.invalidateQueries({ queryKey: interviewKeys.templates });
}

export function useCreateTemplate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: TemplateIn) => unwrap(await api.POST("/api/templates", { body })),
    onSuccess: (template) => {
      toast.success(`${template.name} created`);
      cacheTemplate(queryClient, template);
    },
    onError,
  });
}

export function useUpdateTemplate(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: TemplatePatch) =>
      unwrap(await api.PATCH("/api/templates/{template_id}", { params: { path: { template_id: id } }, body })),
    onSuccess: (template) => {
      toast.success(template.warnings.length ? "Saved, with persona warnings" : "Template saved");
      cacheTemplate(queryClient, template);
    },
    onError,
  });
}

export function useDeleteTemplate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (template: Pick<Template, "id" | "name">) => {
      unwrap(await api.DELETE("/api/templates/{template_id}", { params: { path: { template_id: template.id } } }));
      return template;
    },
    onSuccess: (template) => {
      toast.success(`${template.name} deleted`);
      queryClient.removeQueries({ queryKey: interviewKeys.template(template.id) });
      void queryClient.invalidateQueries({ queryKey: interviewKeys.templates });
    },
    onError,
  });
}

/** The composed interviewer prompt (layers L0–L4) for the saved template. */
export function usePreviewPrompt(id: string) {
  return useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/api/templates/{template_id}/preview-prompt", { params: { path: { template_id: id } } })),
    onError,
  });
}

/** A few interviewer turns against scripted answers (LLM calls; takes a little while). */
export function useSimulate(id: string) {
  return useMutation({
    mutationFn: async (answers: string[]) =>
      unwrap(
        await api.POST("/api/templates/{template_id}/simulate", {
          params: { path: { template_id: id } },
          body: answers.length > 0 ? { answers } : {},
        }),
      ),
    onError,
  });
}

// --- the candidate (no sign-in; the invite token is the credential) ---------------------------------------------

export function useJoinInfo(token: string) {
  return useQuery({
    queryKey: interviewKeys.join(token),
    queryFn: async () => parseJoinInfo(unwrap(await api.GET("/api/join/{token}", { params: { path: { token } } }))),
    retry: false,
    staleTime: 60_000,
  });
}

export function useGiveConsent(token: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (camera: boolean) =>
      unwrap(
        await api.POST("/api/join/{token}/consent", {
          params: { path: { token } },
          body: { accepted: true, camera },
        }),
      ),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: interviewKeys.join(token) }),
    onError,
  });
}

/** Integrity signals over HTTP while the socket is down; true when the batch was delivered. */
export async function postIntegrity(token: string, events: BrowserEvent[]): Promise<boolean> {
  try {
    const result = await api.POST("/api/join/{token}/integrity", {
      params: { path: { token } },
      body: { events },
    });
    return result.response.ok;
  } catch {
    return false;
  }
}
