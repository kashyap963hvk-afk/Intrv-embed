/** Pure helpers for CV profiles: the blind, reviewer-facing view of a structured profile. */

import { z } from "zod";

import type { Schemas } from "@/lib/api/client";
import { SENIORITY, seniorityLabel, type Seniority } from "@/lib/rolepacks";

// Two backend models are named ProfileOut, so the generated schema qualifies them by module.
export type CandidateProfile = Schemas["CandidateProfileOut"];

const named = z.array(z.object({ name: z.string().catch("") })).catch([]);

const structuredSchema = z.object({
  roles: z.array(z.unknown()).catch([]),
  projects: z.array(z.unknown()).catch([]),
  skills: named,
  certifications: z.array(z.string()).catch([]),
  vague_claims: z.array(z.object({ text: z.string().catch(""), why_vague: z.string().catch("") })).catch([]),
  gaps: z.array(z.object({ from: z.string().catch(""), to: z.string().catch("") })).catch([]),
  seniority_estimate: z.enum(SENIORITY).nullable().catch(null),
  total_years: z.number().nonnegative().nullable().catch(null),
});

export interface ProfileSummary {
  seniority: Seniority | null;
  totalYears: number | null;
  roleCount: number;
  projectCount: number;
  skills: string[];
  certifications: string[];
  vagueClaims: { text: string; why: string }[];
  gaps: { from: string; to: string }[];
}

/** What the profile panel shows. Name and contact stay out of it (blind review). */
export function profileSummary(profile: Pick<CandidateProfile, "structured">): ProfileSummary {
  const s = structuredSchema.parse(profile.structured);
  return {
    seniority: s.seniority_estimate,
    totalYears: s.total_years,
    roleCount: s.roles.length,
    projectCount: s.projects.length,
    skills: s.skills.map((skill) => skill.name).filter(Boolean),
    certifications: s.certifications.filter(Boolean),
    vagueClaims: s.vague_claims.filter((c) => c.text).map((c) => ({ text: c.text, why: c.why_vague })),
    gaps: s.gaps.filter((g) => g.from || g.to),
  };
}

/** "Senior · 7 years" style headline for a profile, or "" when nothing is known. */
export function experienceLine(summary: Pick<ProfileSummary, "seniority" | "totalYears">): string {
  const parts: string[] = [];
  if (summary.seniority) parts.push(`${seniorityLabel(summary.seniority)} (estimated)`);
  if (summary.totalYears !== null) {
    const years = Math.round(summary.totalYears * 10) / 10;
    parts.push(`${years} ${years === 1 ? "year" : "years"} of experience`);
  }
  return parts.join(" · ");
}

/** The most recent profile of a document (a re-analysis after the CV changed adds a newer one). */
export function latestProfile(profiles: readonly CandidateProfile[] | undefined): CandidateProfile | null {
  if (!profiles || profiles.length === 0) return null;
  return [...profiles].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
}
