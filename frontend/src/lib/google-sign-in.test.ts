import { describe, expect, it } from "vitest";

import { googleStartUrl } from "./google-sign-in";

describe("googleStartUrl", () => {
  it("points at the API's start endpoint", () => {
    expect(googleStartUrl("https://api.example.com", {})).toBe("https://api.example.com/api/auth/google/start");
  });

  it("carries the invitation, organization and a same-site next path", () => {
    const url = new URL(googleStartUrl("https://api.example.com", { invite: "tok", org: "intrv", next: "/app/llm" }));
    expect(Object.fromEntries(url.searchParams)).toEqual({ invite: "tok", org: "intrv", next: "/app/llm" });
  });

  it("drops next paths that would leave the site", () => {
    for (const next of ["https://evil.example.com", "//evil.example.com"]) {
      expect(new URL(googleStartUrl("https://api.example.com", { next })).searchParams.has("next")).toBe(false);
    }
  });
});
