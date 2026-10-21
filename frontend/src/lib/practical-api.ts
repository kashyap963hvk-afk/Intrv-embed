"use client";

import { useMutation, useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import {
  parsePracticalTask,
  parseRunReport,
  parseSubmitResult,
  parseWhiteboardResult,
  type PracticalTask,
} from "@/lib/practical";
import { parseVoiceInfo } from "@/lib/voice";

/** Candidate endpoints: the invite token in the path is the credential (no sign-in). */
export const candidateKeys = {
  practical: (token: string, topic: number) => ["join", token, "practical", topic] as const,
  voice: (token: string) => ["join", token, "voice"] as const,
};

/** The current practical task; refetched per topic (`topic` is the live topic number). */
export function usePracticalTask(token: string, topic: number, enabled: boolean) {
  return useQuery({
    queryKey: candidateKeys.practical(token, topic),
    queryFn: async (): Promise<PracticalTask> =>
      parsePracticalTask(unwrap(await api.GET("/api/join/{token}/practical", { params: { path: { token } } }))),
    enabled,
    retry: false,
    staleTime: Infinity,
  });
}

export function useRunCode(token: string) {
  return useMutation({
    mutationFn: async (code: string) =>
      parseRunReport(
        unwrap(await api.POST("/api/join/{token}/practical/run", { params: { path: { token } }, body: { code } })),
      ),
  });
}

export function useSubmitPractical(token: string) {
  return useMutation({
    mutationFn: async (body: { code: string; explanation: string }) =>
      parseSubmitResult(
        unwrap(await api.POST("/api/join/{token}/practical/submit", { params: { path: { token } }, body })),
      ),
  });
}

function fileForm(file: Blob, name: string): FormData {
  const form = new FormData();
  form.append("file", file, name);
  return form;
}

export function useSubmitDiagram(token: string) {
  return useMutation({
    mutationFn: async ({ file, name }: { file: Blob; name: string }) =>
      parseWhiteboardResult(
        unwrap(
          await api.POST("/api/join/{token}/whiteboard", {
            params: { path: { token } },
            // The typed body documents the field; the serializer sends the image as multipart/form-data.
            body: { file: name },
            bodySerializer: () => fileForm(file, name),
          }),
        ),
      ),
  });
}

/** STRICT mode with camera consent: one low-resolution frame for the human reviewer; true when it was stored. */
export async function postSnapshot(token: string, jpeg: Blob): Promise<boolean> {
  try {
    const result = await api.POST("/api/join/{token}/snapshot", {
      params: { path: { token } },
      body: { file: "snapshot.jpg" },
      bodySerializer: () => fileForm(jpeg, "snapshot.jpg"),
    });
    return result.response.ok && result.data?.stored === true;
  } catch {
    return false;
  }
}

/** Whether voice is available, the voice service's offer URL and ICE servers (STUN, short-lived TURN). */
export function useVoiceInfo(token: string, enabled: boolean) {
  return useQuery({
    queryKey: candidateKeys.voice(token),
    queryFn: async () =>
      parseVoiceInfo(unwrap(await api.GET("/api/join/{token}/voice", { params: { path: { token } } }))),
    enabled,
    retry: 1,
    // TURN credentials are short-lived: fetch fresh ones for every connection attempt.
    staleTime: 0,
    gcTime: 0,
  });
}
