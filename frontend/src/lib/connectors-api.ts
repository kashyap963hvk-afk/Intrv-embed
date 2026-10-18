"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import {
  intervalLabel,
  OAUTH_CHANNEL,
  parseOAuthMessage,
  providerLabel,
  type ConnectorAccount,
  type CredentialAccountIn,
  type OAuthProvider,
  type OAuthResult,
  type Source,
  type SourceIn,
  type TestResult,
} from "@/lib/connectors";
import { knowledgeKeys, useKnowledgeMutation } from "@/lib/knowledge-api";

export const connectorKeys = {
  all: ["connectors"] as const,
  kinds: ["connectors", "kinds"] as const,
  accounts: ["connectors", "accounts"] as const,
  browse: (accountId: string, path: string | null) => ["connectors", "browse", accountId, path ?? ""] as const,
  serviceAccount: ["connectors", "service-account"] as const,
  sources: knowledgeKeys.sources,
};

export function useConnectorKinds(enabled = true) {
  return useQuery({
    queryKey: connectorKeys.kinds,
    queryFn: async () => unwrap(await api.GET("/api/connectors/kinds")),
    staleTime: 5 * 60_000,
    enabled,
  });
}

export function useConnectorAccounts() {
  return useQuery({
    queryKey: connectorKeys.accounts,
    queryFn: async () => unwrap(await api.GET("/api/connectors/accounts")),
    // Coming back from an OAuth popup focuses this window: pick up the account it connected.
    refetchOnWindowFocus: true,
  });
}

/** One folder of a connected account, page by page ("Load more" follows `next_cursor`). */
export function useBrowse(accountId: string | null, path: string | null) {
  return useInfiniteQuery({
    queryKey: connectorKeys.browse(accountId ?? "", path),
    queryFn: async ({ pageParam }) =>
      unwrap(
        await api.GET("/api/connectors/accounts/{account_id}/browse", {
          params: { path: { account_id: accountId ?? "" }, query: { path, cursor: pageParam } },
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    enabled: accountId !== null,
    staleTime: 30_000,
  });
}

/** Wraps an account mutation with toast feedback and a refresh of the accounts list. */
function useAccountMutation<TVars, TResult>(
  fn: (vars: TVars) => Promise<TResult>,
  success?: (result: TResult, vars: TVars) => void,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (result, vars) => {
      success?.(result, vars);
      void queryClient.invalidateQueries({ queryKey: connectorKeys.accounts });
    },
    onError: (error) => toast.error(error.message),
  });
}

/** Try credentials without saving them; the result is shown next to the form. */
export function useTestCredentials() {
  return useMutation({
    mutationFn: async (body: CredentialAccountIn) => unwrap(await api.POST("/api/connectors/accounts/test", { body })),
    onError: (error) => toast.error(error.message),
  });
}

/** Save credentials; the backend checks them first and refuses ones that do not work. */
export function useCreateAccount() {
  return useAccountMutation(
    async (body: CredentialAccountIn) => unwrap(await api.POST("/api/connectors/accounts", { body })),
    (account) => toast.success(`${account.name} connected`),
  );
}

function reportTest(result: TestResult, account: ConnectorAccount) {
  if (result.ok) toast.success(`${account.name}: ${result.detail}`);
  else toast.error(`${account.name}: ${result.detail}`);
}

export function useCheckAccount() {
  return useAccountMutation(
    async (account: ConnectorAccount) =>
      unwrap(
        await api.POST("/api/connectors/accounts/{account_id}/test", { params: { path: { account_id: account.id } } }),
      ),
    reportTest,
  );
}

export function useDeleteAccount() {
  const queryClient = useQueryClient();
  return useAccountMutation(
    async (account: ConnectorAccount) => {
      unwrap(
        await api.DELETE("/api/connectors/accounts/{account_id}", { params: { path: { account_id: account.id } } }),
      );
      return account;
    },
    (account) => {
      toast.success(`${account.name} disconnected`);
      // Its sources are removed with it (their documents stay).
      void queryClient.invalidateQueries({ queryKey: ["knowledge", "sources"] });
    },
  );
}

/** A short-lived Drive token for the Google Picker, fetched when the user opens it. */
export function usePickerToken() {
  return useMutation({
    mutationFn: async (accountId: string) =>
      unwrap(
        await api.GET("/api/connectors/accounts/{account_id}/picker-token", {
          params: { path: { account_id: accountId } },
        }),
      ),
    onError: (error) => toast.error(error.message),
  });
}

// --- owner: organization Google Drive ---------------------------------------------------------------------------

export function useServiceAccount() {
  return useQuery({
    queryKey: connectorKeys.serviceAccount,
    queryFn: async () => unwrap(await api.GET("/api/connectors/service-account")),
  });
}

export function useCreateGoogleOrgAccount() {
  return useAccountMutation(
    async (body: { name: string; root_folder_ids: string[] }) =>
      unwrap(await api.POST("/api/connectors/accounts/google-org", { body })),
    (account) => toast.success(`${account.name} added`),
  );
}

// --- OAuth popup ------------------------------------------------------------------------------------------------

const POPUP_FEATURES = "popup,width=520,height=720";
const LISTEN_FOR_MS = 10 * 60_000;

/**
 * Connect an OAuth account in a popup. The popup ends on `/app/knowledge/connected`, which posts the outcome to
 * `window.opener` (checked against this origin) and on a same-origin BroadcastChannel, for providers whose pages
 * sever the opener. Accounts are refetched when the outcome arrives and when the popup closes.
 */
export function useOAuthConnect(onConnected?: (result: OAuthResult) => void) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<OAuthProvider | null>(null);
  const stopRef = useRef<(() => void) | null>(null);
  const onConnectedRef = useRef(onConnected);

  useEffect(() => {
    onConnectedRef.current = onConnected;
  }, [onConnected]);

  useEffect(() => () => stopRef.current?.(), []);

  const connect = useCallback(
    (provider: OAuthProvider) => {
      stopRef.current?.();
      // Opened inside the click so popup blockers allow it; it is sent to the provider once the API answers.
      const popup = window.open("", "intrv-oauth", POPUP_FEATURES);
      if (!popup) {
        toast.error("Allow pop-ups for this site to connect an account.");
        return;
      }
      setPending(provider);
      const refresh = () => void queryClient.invalidateQueries({ queryKey: connectorKeys.accounts });
      const channel = typeof BroadcastChannel === "undefined" ? null : new BroadcastChannel(OAUTH_CHANNEL);

      const finish = (result: OAuthResult) => {
        stop();
        refresh();
        if (result.status === "ok") {
          toast.success(`${providerLabel(result.provider)} connected`);
          onConnectedRef.current?.(result);
        } else {
          toast.error(result.detail || `${providerLabel(result.provider)} was not connected.`);
        }
      };
      const onMessage = (event: MessageEvent<unknown>) => {
        if (event.origin !== window.location.origin) return;
        const result = parseOAuthMessage(event.data);
        if (result) finish(result);
      };
      const onChannel = (event: MessageEvent<unknown>) => {
        const result = parseOAuthMessage(event.data);
        if (result) finish(result);
      };
      const watch = setInterval(() => {
        if (!popup.closed) return;
        // Closed, or cut off from this window by the provider: keep listening for the outcome a while longer.
        clearInterval(watch);
        setPending(null);
        refresh();
      }, 500);
      const expire = setTimeout(() => stop(), LISTEN_FOR_MS);

      function stop() {
        clearInterval(watch);
        clearTimeout(expire);
        window.removeEventListener("message", onMessage);
        channel?.close();
        setPending(null);
        if (stopRef.current === stop) stopRef.current = null;
      }

      window.addEventListener("message", onMessage);
      channel?.addEventListener("message", onChannel);
      stopRef.current = stop;

      void api
        .POST("/api/connectors/oauth/{provider}/start", { params: { path: { provider } } })
        .then((result) => {
          popup.location.href = unwrap(result).authorize_url;
        })
        .catch((error: unknown) => {
          popup.close();
          stop();
          toast.error(error instanceof Error ? error.message : "Could not start sign-in.");
        });
    },
    [queryClient],
  );

  return { connect, pending };
}

// --- sources ----------------------------------------------------------------------------------------------------

export function useSources(spaceId: string) {
  return useQuery({
    queryKey: connectorKeys.sources(spaceId),
    queryFn: async () =>
      unwrap(await api.GET("/api/spaces/{space_id}/sources", { params: { path: { space_id: spaceId } } })),
    // Status flips from SYNCING to IDLE/ERROR without a document event, so poll while a sync runs.
    refetchInterval: (query) => (query.state.data?.some((s) => s.status === "SYNCING") ? 5_000 : false),
  });
}

export function useCreateSource(spaceId: string) {
  return useKnowledgeMutation(
    async (body: SourceIn) =>
      unwrap(await api.POST("/api/spaces/{space_id}/sources", { params: { path: { space_id: spaceId } }, body })),
    (source) => (source.sync_enabled ? `Importing ${source.name}; it stays in sync.` : `Importing ${source.name} now…`),
  );
}

export interface SourcePatch {
  sync_enabled?: boolean;
  interval_minutes?: number;
}

export function useUpdateSource() {
  return useKnowledgeMutation(
    async ({ source, patch }: { source: Source; patch: SourcePatch }) => {
      const updated = unwrap(
        await api.PATCH("/api/sources/{source_id}", { params: { path: { source_id: source.id } }, body: patch }),
      );
      return { updated, patch };
    },
    ({ updated, patch }) => {
      if (patch.sync_enabled === undefined) {
        return `${updated.name} now syncs ${intervalLabel(updated.interval_minutes).toLowerCase()}`;
      }
      return updated.sync_enabled ? `${updated.name} stays in sync` : `${updated.name} no longer syncs`;
    },
  );
}

export function useSyncSource() {
  return useKnowledgeMutation(
    async (source: Source) =>
      unwrap(await api.POST("/api/sources/{source_id}/sync", { params: { path: { source_id: source.id } } })),
    (source) => `Syncing ${source.name}…`,
  );
}

export function useDeleteSource() {
  return useKnowledgeMutation(
    async ({ source, deleteDocuments }: { source: Source; deleteDocuments: boolean }) => {
      unwrap(
        await api.DELETE("/api/sources/{source_id}", {
          params: { path: { source_id: source.id }, query: { delete_documents: deleteDocuments } },
        }),
      );
      return { source, deleteDocuments };
    },
    ({ source, deleteDocuments }) =>
      deleteDocuments ? `${source.name} and its documents deleted` : `${source.name} removed; its documents stay`,
  );
}
