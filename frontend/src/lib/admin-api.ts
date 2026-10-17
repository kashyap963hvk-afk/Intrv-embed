"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api, type Schemas } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";

export type AdminUser = Schemas["AdminUserOut"];
export type Group = Schemas["GroupOut"];
export type Invite = Schemas["InviteOut"];
export type UserStatus = AdminUser["status"];

export const adminKeys = {
  users: (status?: string, q?: string) => ["admin", "users", status ?? "all", q ?? ""] as const,
  groups: ["admin", "groups"] as const,
  invites: ["admin", "invites"] as const,
  audit: (action: string) => ["admin", "audit", action] as const,
  org: ["admin", "org"] as const,
};

export function useAdminUsers(status: UserStatus | undefined, q: string) {
  return useQuery({
    queryKey: adminKeys.users(status, q),
    queryFn: async () =>
      unwrap(await api.GET("/api/admin/users", { params: { query: { status: status ?? null, q: q || null } } })),
  });
}

export function useGroups() {
  return useQuery({ queryKey: adminKeys.groups, queryFn: async () => unwrap(await api.GET("/api/admin/groups")) });
}

export function useInvites() {
  return useQuery({ queryKey: adminKeys.invites, queryFn: async () => unwrap(await api.GET("/api/admin/invites")) });
}

/** Wraps a mutation with toast feedback and admin cache invalidation. */
export function useAdminMutation<TVars, TResult>(
  fn: (vars: TVars) => Promise<TResult>,
  successMessage: string | ((result: TResult) => string),
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (result) => {
      toast.success(typeof successMessage === "function" ? successMessage(result) : successMessage);
      void queryClient.invalidateQueries({ queryKey: ["admin"] });
    },
    onError: (error) => toast.error(error.message),
  });
}
