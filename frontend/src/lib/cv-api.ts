"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import { latestProfile } from "@/lib/cv";

export const cvKeys = {
  all: ["cv"] as const,
  profiles: (documentId: string) => ["cv", "profiles", documentId] as const,
};

/** The latest stored profile of a CV document (made when a question set is generated), or null. */
export function useCvProfile(documentId: string | null) {
  return useQuery({
    queryKey: cvKeys.profiles(documentId ?? ""),
    queryFn: async () =>
      unwrap(await api.GET("/api/cv/profiles", { params: { query: { document_id: documentId ?? "" } } })),
    enabled: documentId !== null,
    select: latestProfile,
  });
}
