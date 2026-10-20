"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import { filenameFromDisposition, saveBlob } from "@/lib/download";
import {
  packExportFilename,
  type BuildIn,
  type PackIn,
  type RolePack,
  type RolePackDetail,
  type Validation,
} from "@/lib/rolepacks";

export const rolePackKeys = {
  all: ["role-packs"] as const,
  list: ["role-packs", "list"] as const,
  detail: (packId: string) => ["role-packs", "detail", packId] as const,
  versions: (packId: string) => ["role-packs", "versions", packId] as const,
  validation: (body: string) => ["role-packs", "validation", body] as const,
};

export function useRolePacks() {
  return useQuery({
    queryKey: rolePackKeys.list,
    queryFn: async () => unwrap(await api.GET("/api/role-packs")),
  });
}

export function useRolePack(packId: string) {
  return useQuery({
    queryKey: rolePackKeys.detail(packId),
    queryFn: async () => unwrap(await api.GET("/api/role-packs/{pack_id}", { params: { path: { pack_id: packId } } })),
    retry: false,
  });
}

export function usePackVersions(packId: string) {
  return useQuery({
    queryKey: rolePackKeys.versions(packId),
    queryFn: async () =>
      unwrap(await api.GET("/api/role-packs/{pack_id}/versions", { params: { path: { pack_id: packId } } })),
  });
}

/**
 * Live validation of the editor's content (debounce `body` before passing it). The last result stays shown while
 * the next one loads, so the problems list does not flicker as the user types.
 */
export function useValidation(body: PackIn | null) {
  const key = body ? JSON.stringify(body) : "";
  return useQuery({
    queryKey: rolePackKeys.validation(key),
    queryFn: async (): Promise<Validation> => unwrap(await api.POST("/api/role-packs/validate", { body: body ?? {} })),
    enabled: body !== null,
    staleTime: Infinity,
    gcTime: 30_000,
    retry: false,
    placeholderData: keepPreviousData,
  });
}

/** Wraps a role pack mutation with toast feedback and role pack cache invalidation. */
export function useRolePackMutation<TVars, TResult>(
  fn: (vars: TVars) => Promise<TResult>,
  success?: string | ((result: TResult) => string),
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (result) => {
      if (success) toast.success(typeof success === "function" ? success(result) : success);
      void queryClient.invalidateQueries({ queryKey: rolePackKeys.list });
      void queryClient.invalidateQueries({ queryKey: ["role-packs", "versions"] });
    },
    onError: (error) => toast.error(error.message),
  });
}

function cacheDetail(queryClient: QueryClient, pack: RolePackDetail) {
  queryClient.setQueryData(rolePackKeys.detail(pack.id), pack);
}

export function useCreatePack() {
  const queryClient = useQueryClient();
  return useRolePackMutation(
    async (body: PackIn) => {
      const pack = unwrap(await api.POST("/api/role-packs", { body: { ...body, visibility: "PRIVATE" } }));
      cacheDetail(queryClient, pack);
      return pack;
    },
    (pack) => `${pack.name} saved to your packs`,
  );
}

/** Save a new version. Spec problems of a rejected save come back on the error (see `problemsFromError`). */
export function useUpdatePack(packId: string) {
  const queryClient = useQueryClient();
  return useRolePackMutation(
    async (body: PackIn) => {
      const pack = unwrap(await api.PUT("/api/role-packs/{pack_id}", { params: { path: { pack_id: packId } }, body }));
      cacheDetail(queryClient, pack);
      return pack;
    },
    (pack) => `Saved ${pack.name} (version ${pack.version})`,
  );
}

export function useForkPack() {
  const queryClient = useQueryClient();
  return useRolePackMutation(
    async ({ pack, name }: { pack: Pick<RolePack, "id">; name: string | null }) => {
      const fork = unwrap(
        await api.POST("/api/role-packs/{pack_id}/fork", { params: { path: { pack_id: pack.id } }, body: { name } }),
      );
      cacheDetail(queryClient, fork);
      return fork;
    },
    (fork) => `Forked as ${fork.name}`,
  );
}

export function usePublishPack() {
  const queryClient = useQueryClient();
  return useRolePackMutation(
    async (pack: Pick<RolePack, "id">) => {
      const published = unwrap(
        await api.POST("/api/role-packs/{pack_id}/publish", { params: { path: { pack_id: pack.id } } }),
      );
      cacheDetail(queryClient, published);
      return published;
    },
    (pack) => `${pack.name} is now shared with the organization`,
  );
}

export function useArchivePack() {
  return useRolePackMutation(
    async (pack: Pick<RolePack, "id" | "name">) => {
      unwrap(await api.DELETE("/api/role-packs/{pack_id}", { params: { path: { pack_id: pack.id } } }));
      return pack;
    },
    (pack) => `${pack.name} archived`,
  );
}

/** Download a pack's current version as YAML. */
export function useExportPack() {
  return useMutation({
    mutationFn: async (pack: Pick<RolePack, "id" | "slug" | "version">) => {
      const result = await api.GET("/api/role-packs/{pack_id}/export", {
        params: { path: { pack_id: pack.id } },
        parseAs: "blob",
      });
      const blob = unwrap(result);
      saveBlob(
        blob,
        filenameFromDisposition(result.response.headers.get("Content-Disposition"), packExportFilename(pack)),
      );
    },
    onError: (error) => toast.error(error.message),
  });
}

/** The Role Builder agent: a plain-language description → a validated draft (an LLM call; 10–60 s). */
export function useBuildPack() {
  return useMutation({
    mutationFn: async (body: BuildIn) => unwrap(await api.POST("/api/role-packs/build", { body })),
    onError: (error) => toast.error(error.message),
  });
}
