"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";

import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import type { ProfileCreate } from "@/lib/bridge";
import { probeProfile, useLlmMutation } from "@/lib/llm-api";

/** Under the "llm" prefix, so every model mutation also refreshes device status. */
export const bridgeKeys = {
  devices: ["llm", "bridge", "devices"] as const,
};

/** While pairing, poll quickly until the new PC shows up; otherwise refresh online status now and then. */
export const PAIRING_POLL_MS = 3_000;
export const DEVICES_POLL_MS = 15_000;

export function useBridgeDevices(pairing: boolean) {
  return useQuery({
    queryKey: bridgeKeys.devices,
    queryFn: async () => unwrap(await api.GET("/api/bridge/devices")),
    refetchInterval: pairing ? PAIRING_POLL_MS : DEVICES_POLL_MS,
  });
}

/** A single-use 6-digit code for `intrv-bridge pair` (valid for the number of seconds the API returns). */
export function useCreatePairingCode() {
  return useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/bridge/pair")),
    onError: (error) => toast.error(error.message),
  });
}

export function useRevokeDevice() {
  return useLlmMutation(
    async (device: { id: string; name: string }) => {
      unwrap(await api.DELETE("/api/bridge/devices/{device_id}", { params: { path: { device_id: device.id } } }));
      return device;
    },
    (device) => `${device.name} was revoked. Its bridge disconnects and has to be paired again.`,
  );
}

/** Save a local-model profile, then run the capability probe on it right away. */
export function useCreateLocalProfile() {
  return useLlmMutation(
    async (body: ProfileCreate) => {
      const created = unwrap(await api.POST("/api/llm/profiles", { body }));
      toast.info(`${created.name} saved. Testing it; loading a local model can take a few minutes…`);
      return probeProfile(created.id);
    },
    (p) => (p.status === "OK" ? `${p.name} works` : `${p.name} saved, but the test failed: ${p.last_error ?? ""}`),
  );
}
