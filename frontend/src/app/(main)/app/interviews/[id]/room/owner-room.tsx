"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { InterviewRoomView } from "@/components/interview/interview-room";
import { RequireAuth } from "@/components/auth/require-auth";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { buttonVariants } from "@/components/ui/button";
import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import { parsePlan, type InterviewDetail } from "@/lib/interviews";
import { useInterview } from "@/lib/interviews-api";
import { previewTask, type PracticalTask } from "@/lib/practical";
import { rolePackKeys } from "@/lib/rolepacks-api";

/**
 * The practical task for a plan topic in the preview, from the interview's role pack (prompt and starter code
 * only). The pack is fetched only when the plan has practical topics.
 */
function usePreviewTasks(detail: InterviewDetail | undefined): (topicNumber: number) => PracticalTask | null {
  const topics = parsePlan(detail?.plan).topics;
  const packId = detail?.role_pack_id ?? null;
  const hasPractical = topics.some((t) => t.section === "PRACTICAL" && t.task_id);
  const pack = useQuery({
    queryKey: rolePackKeys.detail(packId ?? ""),
    queryFn: async () =>
      unwrap(await api.GET("/api/role-packs/{pack_id}", { params: { path: { pack_id: packId ?? "" } } })),
    enabled: packId !== null && hasPractical,
    retry: false,
  });
  return (topicNumber) => previewTask(pack.data?.spec, topics[topicNumber - 1]?.task_id ?? null);
}

/** The interview's creator takes (or previews) the interview over the same socket, authenticated by cookie. */
function Room() {
  const { id } = useParams<{ id: string }>();
  const interview = useInterview(id);
  const taskFor = usePreviewTasks(interview.data);

  if (interview.isPending) return <p className="text-sm text-muted-foreground">Loading interview…</p>;
  if (interview.isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Interview unavailable</AlertTitle>
        <AlertDescription>{interview.error.message}</AlertDescription>
      </Alert>
    );
  }
  const detail = interview.data;
  const name = typeof detail.persona.interviewer_name === "string" ? detail.persona.interviewer_name : "Alex";

  return (
    <section className="mx-auto flex w-full max-w-3xl flex-col gap-4">
      <Link href={`/app/interviews/${id}`} className="flex w-fit items-center gap-1 text-sm text-muted-foreground">
        <ArrowLeft className="size-4" aria-hidden /> {detail.title}
      </Link>
      <InterviewRoomView
        sessionId={detail.id}
        token={null}
        interviewerName={name}
        heading={detail.role}
        monitoring={null}
        preview
        previewTask={taskFor}
        endedExtra={
          <Link href={`/app/interviews/${id}/report`} className={buttonVariants()}>
            Open the report
          </Link>
        }
      />
    </section>
  );
}

export function OwnerRoom() {
  return <RequireAuth>{() => <Room />}</RequireAuth>;
}
