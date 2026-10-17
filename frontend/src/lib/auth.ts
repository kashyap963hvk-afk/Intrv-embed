"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Schemas } from "@/lib/api/client";
import { ApiError, unwrap } from "@/lib/api/problem";

export type Me = Schemas["MeResponse"];

export const ME_QUERY_KEY = ["auth", "me"] as const;

/** The signed-in user, or null when signed out. */
export function useMe() {
  return useQuery({
    queryKey: ME_QUERY_KEY,
    queryFn: async (): Promise<Me | null> => {
      const result = await api.GET("/api/auth/me");
      if (result.response.status === 401) return null;
      return unwrap(result);
    },
    staleTime: 30_000,
    retry: (count, error) => !(error instanceof ApiError) && count < 2,
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      await api.POST("/api/auth/logout");
    },
    onSettled: () => {
      queryClient.setQueryData(ME_QUERY_KEY, null);
      queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== "auth" });
    },
  });
}

export function isOwner(me: Me | null | undefined): boolean {
  return me?.user.role === "OWNER" && me.user.status === "ACTIVE";
}
