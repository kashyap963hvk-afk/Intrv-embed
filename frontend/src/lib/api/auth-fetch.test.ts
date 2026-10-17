import { describe, expect, it, vi } from "vitest";

import { createAuthFetch } from "./auth-fetch";

const BASE = "http://api.test.local";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function urlOf(input: RequestInfo | URL): string {
  return input instanceof Request ? input.url : input.toString();
}

describe("createAuthFetch", () => {
  it("adds the CSRF header to mutations but not to reads", async () => {
    const seen: Request[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      if (urlOf(input).endsWith("/api/auth/csrf")) return json(200, { csrf_token: "tok-1" });
      seen.push(input as Request);
      return json(200, {});
    });
    const authFetch = createAuthFetch({ baseUrl: BASE, fetchImpl: fetchImpl as typeof fetch });

    await authFetch(new Request(`${BASE}/api/things`));
    await authFetch(new Request(`${BASE}/api/things`, { method: "POST", body: "{}" }));

    expect(seen[0].headers.get("X-CSRF-Token")).toBeNull();
    expect(seen[1].headers.get("X-CSRF-Token")).toBe("tok-1");
  });

  it("refreshes once on 401 and retries the original request with its body", async () => {
    let authorized = false;
    const bodies: string[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const url = urlOf(input);
      if (url.endsWith("/api/auth/csrf")) return json(200, { csrf_token: "tok" });
      if (url.endsWith("/api/auth/refresh")) {
        authorized = true;
        return new Response(null, { status: 204 });
      }
      bodies.push(await (input as Request).text());
      return authorized ? json(200, { ok: true }) : json(401, { type: "x", status: 401, title: "Unauthorized" });
    });
    const authFetch = createAuthFetch({ baseUrl: BASE, fetchImpl: fetchImpl as typeof fetch });

    const response = await authFetch(new Request(`${BASE}/api/items`, { method: "POST", body: '{"a":1}' }));

    expect(response.status).toBe(200);
    expect(bodies).toEqual(['{"a":1}', '{"a":1}']);
  });

  it("shares a single refresh between concurrent 401s", async () => {
    let refreshes = 0;
    let authorized = false;
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const url = urlOf(input);
      if (url.endsWith("/api/auth/csrf")) return json(200, { csrf_token: "tok" });
      if (url.endsWith("/api/auth/refresh")) {
        refreshes += 1;
        await new Promise((resolve) => setTimeout(resolve, 10));
        authorized = true;
        return new Response(null, { status: 204 });
      }
      return authorized ? json(200, {}) : json(401, {});
    });
    const authFetch = createAuthFetch({ baseUrl: BASE, fetchImpl: fetchImpl as typeof fetch });

    const results = await Promise.all([1, 2, 3].map(() => authFetch(new Request(`${BASE}/api/me`))));

    expect(results.map((r) => r.status)).toEqual([200, 200, 200]);
    expect(refreshes).toBe(1);
  });

  it("does not try to refresh when signing in fails", async () => {
    const urls: string[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const url = urlOf(input);
      urls.push(url);
      if (url.endsWith("/api/auth/csrf")) return json(200, { csrf_token: "tok" });
      return json(401, {});
    });
    const authFetch = createAuthFetch({ baseUrl: BASE, fetchImpl: fetchImpl as typeof fetch });

    const response = await authFetch(new Request(`${BASE}/api/auth/login`, { method: "POST", body: "{}" }));

    expect(response.status).toBe(401);
    expect(urls.some((u) => u.endsWith("/api/auth/refresh"))).toBe(false);
  });

  it("re-fetches the CSRF token once when the server rejects it", async () => {
    let csrfCalls = 0;
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const url = urlOf(input);
      if (url.endsWith("/api/auth/csrf")) {
        csrfCalls += 1;
        return json(200, { csrf_token: `tok-${csrfCalls}` });
      }
      const token = (input as Request).headers.get("X-CSRF-Token");
      return token === "tok-2" ? json(200, {}) : json(403, { detail: "Missing or invalid CSRF token." });
    });
    const authFetch = createAuthFetch({ baseUrl: BASE, fetchImpl: fetchImpl as typeof fetch });

    const response = await authFetch(new Request(`${BASE}/api/x`, { method: "DELETE" }));

    expect(response.status).toBe(200);
    expect(csrfCalls).toBe(2);
  });

  it("shares one CSRF fetch between concurrent mutations", async () => {
    let csrfCalls = 0;
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      if (urlOf(input).endsWith("/api/auth/csrf")) {
        csrfCalls += 1;
        await new Promise((resolve) => setTimeout(resolve, 5));
        return json(200, { csrf_token: `tok-${csrfCalls}` });
      }
      return json(200, { token: (input as Request).headers.get("X-CSRF-Token") });
    });
    const authFetch = createAuthFetch({ baseUrl: BASE, fetchImpl: fetchImpl as typeof fetch });

    const responses = await Promise.all(
      [1, 2, 3].map(() => authFetch(new Request(`${BASE}/api/x`, { method: "POST", body: "{}" }))),
    );
    const tokens = await Promise.all(responses.map(async (r) => ((await r.json()) as { token: string }).token));

    expect(csrfCalls).toBe(1);
    expect(new Set(tokens)).toEqual(new Set(["tok-1"]));
  });
});
