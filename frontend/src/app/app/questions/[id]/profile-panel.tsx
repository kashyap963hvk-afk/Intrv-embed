"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { experienceLine, profileSummary, type CandidateProfile } from "@/lib/cv";

const MAX_SKILLS = 24;

/** The blind candidate profile (no name or contact): summary, experience, domain tags, vague claims and gaps. */
export function ProfilePanel({ profile, loading }: { profile: CandidateProfile | null; loading: boolean }) {
  if (loading) return <p className="text-sm text-muted-foreground">Loading the candidate profile…</p>;
  if (!profile) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Candidate profile</CardTitle>
          <CardDescription>Appears once generation has read the CV.</CardDescription>
        </CardHeader>
      </Card>
    );
  }
  const summary = profileSummary(profile);
  const experience = experienceLine(summary);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Candidate profile</CardTitle>
        <CardDescription>
          Blind summary for fair review: no name, contact details or other personal cues.
          {experience && ` ${experience}.`}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 text-sm">
        {profile.blind_summary && <p className="leading-relaxed">{profile.blind_summary}</p>}
        <p className="text-xs text-muted-foreground">
          {summary.roleCount} {summary.roleCount === 1 ? "role" : "roles"} · {summary.projectCount}{" "}
          {summary.projectCount === 1 ? "project" : "projects"}
          {summary.certifications.length > 0 && ` · ${summary.certifications.length} certifications`}
        </p>
        {profile.domain_tags.length > 0 && (
          <div className="flex flex-col gap-1">
            <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Domain tags</p>
            <div className="flex flex-wrap gap-1">
              {profile.domain_tags.map((tag) => (
                <Badge key={tag} variant="secondary" className="font-mono">
                  {tag}
                </Badge>
              ))}
            </div>
          </div>
        )}
        {summary.skills.length > 0 && (
          <div className="flex flex-col gap-1">
            <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Skills</p>
            <div className="flex flex-wrap gap-1">
              {summary.skills.slice(0, MAX_SKILLS).map((skill, i) => (
                <Badge key={`${skill}-${i}`} variant="outline">
                  {skill}
                </Badge>
              ))}
              {summary.skills.length > MAX_SKILLS && (
                <span className="text-xs text-muted-foreground">+{summary.skills.length - MAX_SKILLS} more</span>
              )}
            </div>
          </div>
        )}
        {summary.vagueClaims.length > 0 && (
          <div className="flex flex-col gap-1">
            <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Vague claims to probe</p>
            <ul className="flex flex-col gap-2">
              {summary.vagueClaims.map((claim, i) => (
                <li key={i} className="rounded-md bg-muted/50 px-3 py-2">
                  <p>{claim.text}</p>
                  {claim.why && <p className="text-xs text-muted-foreground">{claim.why}</p>}
                </li>
              ))}
            </ul>
          </div>
        )}
        {summary.gaps.length > 0 && (
          <p className="text-xs text-muted-foreground">
            Gaps in the timeline: {summary.gaps.map((gap) => [gap.from, gap.to].filter(Boolean).join(" – ")).join(", ")}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
