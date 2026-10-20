import { describe, expect, it } from "vitest";

import { experienceLine, latestProfile, profileSummary, type CandidateProfile } from "./cv";

function profile(overrides: Partial<CandidateProfile> = {}): CandidateProfile {
  return {
    id: "p1",
    document_id: "d1",
    document_version: 1,
    blind_summary: "Firmware engineer.",
    contact: { email: null, phone: null, links: [] },
    domain_tags: ["embedded"],
    evidence_index: {},
    structured: {},
    created_at: "2026-10-01T10:00:00Z",
    ...overrides,
  };
}

describe("profileSummary", () => {
  it("reads the structured profile with defaults", () => {
    const summary = profileSummary(
      profile({
        structured: {
          roles: [{}, {}],
          projects: [{}],
          skills: [{ name: "C" }, { name: "" }],
          vague_claims: [{ text: "Led many projects", why_vague: "no scope" }, { text: "" }],
          gaps: [{ from: "2019", to: "2020" }, {}],
          seniority_estimate: "SENIOR",
          total_years: 7,
          certifications: ["ISO 26262"],
        },
      }),
    );
    expect(summary).toEqual({
      seniority: "SENIOR",
      totalYears: 7,
      roleCount: 2,
      projectCount: 1,
      skills: ["C"],
      certifications: ["ISO 26262"],
      vagueClaims: [{ text: "Led many projects", why: "no scope" }],
      gaps: [{ from: "2019", to: "2020" }],
    });
    expect(profileSummary(profile({ structured: { seniority_estimate: "GURU" } })).seniority).toBeNull();
  });

  it("writes the experience line", () => {
    expect(experienceLine({ seniority: "MID", totalYears: 1 })).toBe("Mid (estimated) · 1 year of experience");
    expect(experienceLine({ seniority: null, totalYears: 4.25 })).toBe("4.3 years of experience");
    expect(experienceLine({ seniority: null, totalYears: null })).toBe("");
  });
});

describe("latestProfile", () => {
  it("picks the newest profile", () => {
    const older = profile({ id: "old", created_at: "2026-09-01T10:00:00Z" });
    const newer = profile({ id: "new", created_at: "2026-10-02T10:00:00Z" });
    expect(latestProfile([older, newer])?.id).toBe("new");
    expect(latestProfile([])).toBeNull();
    expect(latestProfile(undefined)).toBeNull();
  });
});
