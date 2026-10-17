"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api, type Schemas } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";

export type Preset = Schemas["PresetOut"];
export type Profile = Schemas["ProfileOut"];
export type AgentRole = Schemas["AgentRole"];
export type Routes = Schemas["RoutesOut"];

export const AGENT_ROLES: { role: AgentRole; label: string; help: string }[] = [
  { role: "interviewer", label: "Interviewer", help: "Talks to the candidate. Needs a fast first token." },
  { role: "controller", label: "Turn controller", help: "Decides the next move each turn. Fast, reliable JSON." },
  { role: "grader", label: "Grader", help: "Scores answers with evidence. Use your strongest model." },
  { role: "planner", label: "Planner", help: "Plans interviews from the CV, JD and role pack." },
  { role: "question_gen", label: "Question generator", help: "Writes grounded question sets." },
  { role: "extractor", label: "CV extractor", help: "Turns CVs into structured profiles." },
  { role: "vision", label: "Vision", help: "Reads whiteboards, diagrams and scanned pages." },
  { role: "role_builder", label: "Role builder", help: "Drafts role packs from a description." },
];

export const llmKeys = {
  presets: ["llm", "presets"] as const,
  profiles: ["llm", "profiles"] as const,
  routes: ["llm", "routes"] as const,
};

export function usePresets() {
  return useQuery({
    queryKey: llmKeys.presets,
    queryFn: async () => unwrap(await api.GET("/api/llm/presets")),
    staleTime: 5 * 60_000,
  });
}

export function useProfiles() {
  return useQuery({ queryKey: llmKeys.profiles, queryFn: async () => unwrap(await api.GET("/api/llm/profiles")) });
}

export function useRoutes() {
  return useQuery({ queryKey: llmKeys.routes, queryFn: async () => unwrap(await api.GET("/api/llm/routes")) });
}

export function useLlmMutation<TVars, TResult>(
  fn: (vars: TVars) => Promise<TResult>,
  success?: string | ((result: TResult) => string),
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (result) => {
      if (success) toast.success(typeof success === "function" ? success(result) : success);
      void queryClient.invalidateQueries({ queryKey: ["llm"] });
    },
    onError: (error) => toast.error(error.message),
  });
}

export function probeProfile(id: string) {
  return api.POST("/api/llm/profiles/{profile_id}/probe", { params: { path: { profile_id: id } } }).then(unwrap);
}
