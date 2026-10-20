"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { InterviewRoomView } from "@/components/interview/interview-room";
import { RequireAuth } from "@/components/auth/require-auth";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { buttonVariants } from "@/components/ui/button";
import { useInterview } from "@/lib/interviews-api";

/** The interview's creator takes (or previews) the interview over the same socket, authenticated by cookie. */
function Room() {
  const { id } = useParams<{ id: string }>();
  const interview = useInterview(id);

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
