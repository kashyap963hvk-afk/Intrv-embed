"use client";

import { useParams } from "next/navigation";
import { useState, type ReactNode } from "react";

import { InterviewRoomView } from "@/components/interview/interview-room";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import type { JoinInfo } from "@/lib/interviews";
import { postIntegrity, useGiveConsent, useJoinInfo } from "@/lib/interviews-api";

function Notice({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card className="mx-auto w-full max-w-lg">
      <CardContent className="flex flex-col gap-2">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        <div className="flex flex-col gap-2 text-sm text-muted-foreground">{children}</div>
      </CardContent>
    </Card>
  );
}

function Consent({ token, info, onAccepted }: { token: string; info: JoinInfo; onAccepted: () => void }) {
  const [agreed, setAgreed] = useState(false);
  const [camera, setCamera] = useState(false);
  const consent = useGiveConsent(token);
  const cameraNeeded = info.camera_monitoring === "required";
  const ready = agreed && (!cameraNeeded || camera);

  return (
    <Card className="w-full">
      <CardContent className="flex flex-col gap-6">
        <div className="flex flex-col gap-1">
          <p className="text-sm text-muted-foreground">{info.organization}</p>
          <h1 className="text-2xl font-semibold tracking-tight">{info.title || "Your interview"}</h1>
        </div>

        <dl className="grid gap-3 text-sm sm:grid-cols-3">
          <div className="flex flex-col gap-0.5">
            <dt className="text-muted-foreground">Interviewer</dt>
            <dd className="font-medium">{info.interviewer_name}, an AI interviewer</dd>
          </div>
          <div className="flex flex-col gap-0.5">
            <dt className="text-muted-foreground">Duration</dt>
            <dd className="font-medium">About {info.duration_minutes} minutes</dd>
          </div>
          <div className="flex flex-col gap-0.5">
            <dt className="text-muted-foreground">Format</dt>
            <dd className="font-medium">
              {info.mode === "TEXT" ? "Text: you type your answers" : info.mode.toLowerCase()}
            </dd>
          </div>
        </dl>

        <section className="flex flex-col gap-2">
          <h2 className="font-semibold">What is recorded</h2>
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {info.monitoring.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>

        <section className="flex flex-col gap-2 text-sm">
          <h2 className="font-semibold">Your data and your choices</h2>
          {info.retention_days !== null && <p>Interview data is kept for {info.retention_days} days.</p>}
          {info.contact && <p>Questions about the interview or your data: {info.contact}.</p>}
          {info.human_alternative && <p>{info.human_alternative}</p>}
        </section>

        <div className="flex flex-col gap-3 rounded-lg border p-4">
          {info.camera_monitoring !== "off" && (
            <label className="flex items-start gap-3 text-sm">
              <Checkbox className="mt-0.5" checked={camera} onCheckedChange={(c) => setCamera(c === true)} />
              <span>
                I allow the camera check described above
                {cameraNeeded ? " (required for this interview)." : " (optional)."}
              </span>
            </label>
          )}
          <label className="flex items-start gap-3 text-sm">
            <Checkbox className="mt-0.5" checked={agreed} onCheckedChange={(c) => setAgreed(c === true)} />
            <span>
              I have read the information above and agree to take this interview with an AI interviewer on these terms.
            </span>
          </label>
          <Button
            className="w-fit"
            disabled={!ready || consent.isPending}
            onClick={() => consent.mutate(camera, { onSuccess: onAccepted })}
          >
            {consent.isPending ? "Saving…" : "I agree and continue"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function Join({ token }: { token: string }) {
  const info = useJoinInfo(token);
  const [accepted, setAccepted] = useState(false);

  if (info.isPending) return <p className="text-sm text-muted-foreground">Loading your interview…</p>;
  if (info.isError) {
    return (
      <Notice title="This link does not open an interview">
        <p>{info.error.message}</p>
        <p>Please contact the person who sent you the link; they can send you a new one.</p>
      </Notice>
    );
  }
  const data = info.data;
  if (data.status === "COMPLETED") {
    return (
      <Notice title="Thank you for your time">
        <p>This interview has ended. The hiring team will review the conversation and get back to you.</p>
      </Notice>
    );
  }
  if (data.status === "ABORTED") {
    return (
      <Notice title="This interview was cancelled">
        <p>Please contact {data.contact || "the person who sent you the link"} if you have questions.</p>
      </Notice>
    );
  }
  if (data.status === "FAILED") {
    return (
      <Notice title="This interview is not available yet">
        <p>It could not be prepared. Please contact {data.contact || "the person who sent you the link"}.</p>
      </Notice>
    );
  }
  if (data.status === "PREPARING") {
    return (
      <Notice title="Almost ready">
        <p>The interview is still being prepared. Please try again in a few minutes.</p>
        <Button className="w-fit" variant="outline" disabled={info.isFetching} onClick={() => void info.refetch()}>
          Check again
        </Button>
      </Notice>
    );
  }
  if (!accepted && !data.consent_given) {
    return <Consent token={token} info={data} onAccepted={() => setAccepted(true)} />;
  }
  return (
    <InterviewRoomView
      sessionId={data.session_id}
      token={token}
      interviewerName={data.interviewer_name}
      heading={data.title}
      monitoring={{ enabled: data.integrity_mode !== "OFF", items: data.monitoring }}
      deliverFallback={(events) => postIntegrity(token, events)}
    />
  );
}

export function JoinPage() {
  const { token } = useParams<{ token: string }>();
  return <Join token={token} />;
}
