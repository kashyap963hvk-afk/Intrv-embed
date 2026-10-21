"use client";

import { FileImage, FileText, Keyboard, LoaderCircle, Play, SendHorizontal } from "lucide-react";
import dynamic from "next/dynamic";
import { useEffect, useRef, useState, type ClipboardEvent, type ReactNode, type RefObject } from "react";

import { RunResults } from "@/components/practical/run-results";
import type { BoardHandle } from "@/components/practical/excalidraw-board";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api/problem";
import {
  answerMeta,
  EMPTY_TYPING,
  pastedChars,
  trackInput,
  trackKey,
  trackPaste,
  type AnswerMeta,
  type TypingTracker,
} from "@/lib/interviews";
import {
  diagramProblem,
  editorLanguage,
  explanationProblem,
  isCodeTask,
  languageLabel,
  PRACTICAL_LIMITS,
  practicalErrorMessage,
  taskKindLabel,
  type PracticalTask,
} from "@/lib/practical";
import { usePracticalTask, useRunCode, useSubmitDiagram, useSubmitPractical } from "@/lib/practical-api";
import { useSessionDraft } from "@/lib/use-session-draft";

const CodeEditor = dynamic(() => import("@/components/practical/code-editor"), {
  ssr: false,
  loading: () => <p className="p-3 text-sm text-muted-foreground">Loading the editor…</p>,
});

const ExcalidrawBoard = dynamic(() => import("@/components/practical/excalidraw-board"), {
  ssr: false,
  loading: () => <p className="p-3 text-sm text-muted-foreground">Loading the whiteboard…</p>,
});

/** How the panel talks back to the interview: the explanation goes in as an answer linked to the submission. */
export interface PracticalAnswering {
  send: (text: string, meta: AnswerMeta) => boolean;
  canAnswer: boolean;
  /** Why answering is not possible right now. */
  hint: string;
  /** Integrity: size of a paste into the editor or explanation (never its content). */
  onPaste: (chars: number) => void;
  /** A message that must outlive the task panel (the topic moves on once the explanation is answered). */
  onNotice: (message: string) => void;
}

function TaskHeader({ task, children }: { task: PracticalTask; children?: ReactNode }) {
  const language = languageLabel(task.language);
  return (
    <section aria-label="Practical task" className="flex flex-col gap-4 rounded-lg border p-4">
      <div className="flex flex-col gap-2">
        <h2 className="flex flex-wrap items-center gap-2 font-semibold">
          {taskKindLabel(task.kind)}
          {language && <Badge variant="secondary">{language}</Badge>}
          {task.test_count > 0 && <Badge variant="outline">{task.test_count} tests</Badge>}
        </h2>
        <p className="text-sm leading-relaxed whitespace-pre-wrap">{task.prompt}</p>
      </div>
      {task.reference.length > 0 && (
        <div className="flex flex-col gap-2">
          <h3 className="flex items-center gap-1.5 text-sm font-medium">
            <FileText className="size-4" aria-hidden /> From the datasheet
          </h3>
          {task.reference.map((excerpt, index) => (
            <figure key={`${excerpt.document_id}-${index}`} className="rounded-md border bg-muted/40 px-3 py-2">
              {excerpt.title && <figcaption className="text-xs font-medium">{excerpt.title}</figcaption>}
              <blockquote className="mt-1 max-h-72 overflow-y-auto font-mono text-xs leading-relaxed whitespace-pre-wrap">
                {excerpt.text}
              </blockquote>
            </figure>
          ))}
        </div>
      )}
      {children}
    </section>
  );
}

/** The candidate's explanation, with the same typing meta as the answer box (sizes and counts only). */
function ExplanationField({
  id,
  value,
  onChange,
  trackerRef,
  onPaste,
  placeholder,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  trackerRef: RefObject<TypingTracker>;
  onPaste: (chars: number) => void;
  placeholder: string;
}) {
  const paste = useRef<{ before: number; selection: number } | null>(null);
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        Your explanation
      </label>
      <Textarea
        id={id}
        rows={4}
        value={value}
        maxLength={PRACTICAL_LIMITS.explanationMax}
        placeholder={placeholder}
        onKeyDown={() => {
          trackerRef.current = trackKey(trackerRef.current, Date.now());
        }}
        onPaste={(event: ClipboardEvent<HTMLTextAreaElement>) => {
          const el = event.currentTarget;
          paste.current = { before: el.value.length, selection: (el.selectionEnd ?? 0) - (el.selectionStart ?? 0) };
        }}
        onChange={(event) => {
          const next = event.target.value;
          trackerRef.current = trackInput(trackerRef.current, Date.now());
          if (paste.current) {
            const chars = pastedChars(paste.current.before, paste.current.selection, next.length);
            trackerRef.current = trackPaste(trackerRef.current, chars);
            onPaste(chars);
            paste.current = null;
          }
          onChange(next);
        }}
      />
    </div>
  );
}

/** Send the explanation as an answer linked to the submission; it can be sent again if the socket was down. */
function useLinkedAnswer(answering: PracticalAnswering) {
  const [unsent, setUnsent] = useState<{ text: string; meta: AnswerMeta } | null>(null);
  const [sent, setSent] = useState(false);
  const send = (text: string, meta: AnswerMeta) => {
    if (answering.send(text, meta)) {
      setUnsent(null);
      setSent(true);
      return true;
    }
    setUnsent({ text, meta });
    return false;
  };
  return { unsent, sent, send, retry: () => unsent && send(unsent.text, unsent.meta) };
}

function SentNote({ linked, canAnswer }: { linked: ReturnType<typeof useLinkedAnswer>; canAnswer: boolean }) {
  if (linked.unsent) {
    return (
      <div role="status" className="flex flex-wrap items-center gap-2 rounded-md bg-muted px-3 py-2 text-sm">
        Your work is saved. The explanation is not sent yet because the connection dropped.
        <Button size="sm" variant="outline" disabled={!canAnswer} onClick={() => linked.retry()}>
          Send the explanation
        </Button>
      </div>
    );
  }
  if (linked.sent) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Submitted. The interviewer will talk it through with you.
      </p>
    );
  }
  return null;
}

function CodeWorkspace({
  token,
  task,
  answering,
}: {
  token: string;
  task: PracticalTask;
  answering: PracticalAnswering;
}) {
  const [code, setCode] = useSessionDraft(`intrv.practical.${token}.${task.task_id}`, task.starter_code);
  const [explanation, setExplanation] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const tracker = useRef(EMPTY_TYPING);
  const run = useRunCode(token);
  const submit = useSubmitPractical(token);
  const linked = useLinkedAnswer(answering);
  const runError = run.error ? practicalErrorMessage(run.error) : null;
  const submitError = submit.error ? practicalErrorMessage(submit.error) : null;

  function onSubmit() {
    const issue = explanationProblem(explanation);
    setProblem(issue);
    if (issue || !answering.canAnswer) return;
    const text = explanation.trim();
    const meta = answerMeta(tracker.current, Date.now());
    submit.mutate(
      { code: code.slice(0, PRACTICAL_LIMITS.codeMax), explanation: text },
      {
        onSuccess: (result) => {
          if (linked.send(text, { ...meta, submission_id: result.submission_id })) {
            setExplanation("");
            tracker.current = EMPTY_TYPING;
          }
        },
      },
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="overflow-hidden rounded-md border">
        <CodeEditor
          value={code}
          language={editorLanguage(task.language)}
          onChange={setCode}
          onPaste={answering.onPaste}
          label={`Code editor (${languageLabel(task.language) || "plain text"})`}
        />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {task.can_run && (
          <Button variant="outline" disabled={run.isPending} onClick={() => run.mutate(code)}>
            {run.isPending ? <LoaderCircle className="animate-spin" aria-hidden /> : <Play aria-hidden />}
            {task.test_count > 0 ? "Run tests" : "Compile"}
          </Button>
        )}
        <Button variant="ghost" size="sm" onClick={() => setCode(task.starter_code)}>
          Reset to the starter code
        </Button>
      </div>
      {runError && (
        <p role="alert" className="text-sm text-destructive">
          {runError.message}
        </p>
      )}
      {run.data && !run.isPending && <RunResults report={run.data} />}

      <ExplanationField
        id={`explain-${task.task_id}`}
        value={explanation}
        onChange={setExplanation}
        trackerRef={tracker}
        onPaste={answering.onPaste}
        placeholder="What did you find or change, and why?"
      />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          {answering.canAnswer ? "Submitting sends your code and this explanation to the interviewer." : answering.hint}
        </p>
        <Button disabled={!answering.canAnswer || submit.isPending} onClick={onSubmit}>
          {submit.isPending ? <LoaderCircle className="animate-spin" aria-hidden /> : <SendHorizontal aria-hidden />}
          Submit
        </Button>
      </div>
      {problem && (
        <p role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      )}
      {submitError && (
        <p role="alert" className="text-sm text-destructive">
          {submitError.message}
          {submitError.explainInstead && " Use the answer box below."}
        </p>
      )}
      <SentNote linked={linked} canAnswer={answering.canAnswer} />
    </div>
  );
}

function DiagramWorkspace({
  token,
  task,
  answering,
}: {
  token: string;
  task: PracticalTask;
  answering: PracticalAnswering;
}) {
  const [source, setSource] = useState<"draw" | "upload">("draw");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const previewRef = useRef<string | null>(null);
  const [explanation, setExplanation] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const board = useRef<BoardHandle | null>(null);
  const tracker = useRef(EMPTY_TYPING);
  const upload = useSubmitDiagram(token);
  const linked = useLinkedAnswer(answering);
  const uploadError = upload.error ? practicalErrorMessage(upload.error) : null;

  useEffect(
    () => () => {
      if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    },
    [],
  );

  function pick(picked: File | null) {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    previewRef.current = picked ? URL.createObjectURL(picked) : null;
    setPreview(previewRef.current);
    setFile(picked);
    setProblem(picked ? diagramProblem(picked) : null);
  }

  async function onSubmit() {
    const issue = explanationProblem(explanation);
    if (issue) {
      setProblem(issue);
      return;
    }
    let image: { blob: Blob; name: string } | null = null;
    if (source === "upload") {
      const fileIssue = file ? diagramProblem(file) : "Choose an image of your diagram first.";
      if (fileIssue || !file) {
        setProblem(fileIssue);
        return;
      }
      image = { blob: file, name: file.name };
    } else {
      setExporting(true);
      try {
        const png = (await board.current?.exportPng()) ?? null;
        image = png ? { blob: png, name: "diagram.png" } : null;
      } finally {
        setExporting(false);
      }
      if (!image) {
        setProblem("Draw your diagram on the whiteboard first (or upload a photo of it).");
        return;
      }
    }
    setProblem(null);
    if (!answering.canAnswer) return;
    const text = explanation.trim();
    const meta = answerMeta(tracker.current, Date.now());
    upload.mutate(
      { file: image.blob, name: image.name },
      {
        onSuccess: (result) => {
          if (result.message) answering.onNotice(result.message);
          if (linked.send(text, { ...meta, submission_id: result.submission_id })) {
            setExplanation("");
            tracker.current = EMPTY_TYPING;
          }
        },
      },
    );
  }

  const busy = exporting || upload.isPending;
  return (
    <div className="flex flex-col gap-3">
      <Tabs value={source} onValueChange={(value) => setSource(value === "upload" ? "upload" : "draw")}>
        <TabsList>
          <TabsTrigger value="draw">Whiteboard</TabsTrigger>
          <TabsTrigger value="upload">
            <FileImage aria-hidden /> Upload a photo
          </TabsTrigger>
        </TabsList>
        <TabsContent value="draw" forceMount className="data-[state=inactive]:hidden">
          <ExcalidrawBoard
            onReady={(handle) => {
              board.current = handle;
            }}
          />
        </TabsContent>
        <TabsContent value="upload" className="flex flex-col gap-2">
          <label htmlFor={`diagram-${task.task_id}`} className="text-sm">
            A photo or scan of a hand-drawn diagram (PNG, JPEG or WebP, up to 3 MB).
          </label>
          <Input
            id={`diagram-${task.task_id}`}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            onChange={(event) => pick(event.target.files?.[0] ?? null)}
          />
          {preview && (
            // A local preview of the candidate's own file (an object URL), not a remote image.
            // eslint-disable-next-line @next/next/no-img-element
            <img src={preview} alt="Your diagram" className="max-h-80 w-fit rounded-md border object-contain" />
          )}
        </TabsContent>
      </Tabs>

      <ExplanationField
        id={`explain-${task.task_id}`}
        value={explanation}
        onChange={setExplanation}
        trackerRef={tracker}
        onPaste={answering.onPaste}
        placeholder="Talk the interviewer through your diagram."
      />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          {answering.canAnswer ? "Submitting sends the diagram and your explanation." : answering.hint}
        </p>
        <Button disabled={!answering.canAnswer || busy} onClick={() => void onSubmit()}>
          {busy ? <LoaderCircle className="animate-spin" aria-hidden /> : <SendHorizontal aria-hidden />}
          Submit diagram
        </Button>
      </div>
      {problem && (
        <p role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      )}
      {uploadError && (
        <p role="alert" className="text-sm text-destructive">
          {uploadError.message}
        </p>
      )}
      <SentNote linked={linked} canAnswer={answering.canAnswer} />
    </div>
  );
}

function Loading({ children }: { children: ReactNode }) {
  return (
    <div role="status" className="flex items-center gap-2 rounded-lg border p-4 text-sm text-muted-foreground">
      {children}
    </div>
  );
}

/**
 * The candidate's practical task for the current PRACTICAL topic: a code editor with run and submit, a whiteboard
 * with an upload alternative, or datasheet excerpts. In voice mode the task is shown and the candidate is asked to
 * switch to text, because submissions are linked through a typed answer.
 */
export function CandidatePractical({
  token,
  topic,
  answering,
  voice,
}: {
  token: string;
  topic: number;
  answering: PracticalAnswering;
  /** Voice mode: switch to text to work on the task. */
  voice: { onSwitchToText: () => void } | null;
}) {
  const query = usePracticalTask(token, topic, true);

  if (query.isPending) {
    return (
      <Loading>
        <LoaderCircle className="size-4 animate-spin" aria-hidden /> Loading the task…
      </Loading>
    );
  }
  if (query.isError) {
    const none = query.error instanceof ApiError && query.error.problem.status === 409;
    return (
      <Loading>
        {none ? "There is no practical task open right now; answer in the chat below." : query.error.message}
      </Loading>
    );
  }
  const task = query.data;
  if (voice) {
    return (
      <TaskHeader task={task}>
        {task.kind !== "DATASHEET_READING" && (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-muted px-3 py-2 text-sm">
            <span className="flex items-center gap-1.5">
              <Keyboard className="size-4" aria-hidden />
              This task needs the keyboard. Switch to text to work on it; you can come back to voice afterwards.
            </span>
            <Button size="sm" onClick={voice.onSwitchToText}>
              Switch to text
            </Button>
          </div>
        )}
      </TaskHeader>
    );
  }
  return (
    <TaskHeader task={task}>
      {isCodeTask(task.kind) && <CodeWorkspace key={task.task_id} token={token} task={task} answering={answering} />}
      {task.kind === "WHITEBOARD" && (
        <DiagramWorkspace key={task.task_id} token={token} task={task} answering={answering} />
      )}
      {task.kind === "DATASHEET_READING" && (
        <p className="text-sm text-muted-foreground">Answer the question in the chat below.</p>
      )}
    </TaskHeader>
  );
}

/** The owner's preview: what the candidate sees, read-only (running and submitting need the candidate's link). */
export function PreviewPractical({ task }: { task: PracticalTask | null }) {
  if (!task) {
    return <Loading>This practical task is not in the role pack any more; answer in the chat below.</Loading>;
  }
  return (
    <TaskHeader task={task}>
      {isCodeTask(task.kind) && (
        <div className="overflow-hidden rounded-md border">
          <CodeEditor
            value={task.starter_code}
            language={editorLanguage(task.language)}
            readOnly
            label="Starter code (read-only)"
          />
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        Preview: the candidate gets an editor with run and submit, or a whiteboard, on their invitation link. Here,
        answer in the chat below.
      </p>
    </TaskHeader>
  );
}
