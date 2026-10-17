/**
 * fetch wrapper used by the typed API client:
 *  - sends the CSRF token (double-submit) on state-changing requests, fetching one when needed;
 *  - on 401, refreshes the session once (concurrent callers share one refresh) and retries.
 */

const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const NO_REFRESH_PATHS = ["/api/auth/login", "/api/auth/refresh", "/api/auth/logout", "/api/auth/csrf"];

export interface AuthFetchOptions {
  baseUrl: string;
  fetchImpl?: typeof fetch;
}

export function createAuthFetch({ baseUrl, fetchImpl = (...args) => fetch(...args) }: AuthFetchOptions) {
  let csrfToken: string | null = null;
  let csrfInFlight: Promise<string> | null = null;
  let refreshInFlight: Promise<boolean> | null = null;

  // Concurrent callers share one fetch: two parallel fetches would each set a different cookie, and the
  // cached token could then disagree with whichever cookie the browser kept.
  function getCsrf(force = false): Promise<string> {
    if (csrfToken && !force) return Promise.resolve(csrfToken);
    csrfInFlight ??= (async () => {
      const response = await fetchImpl(`${baseUrl}/api/auth/csrf`, { credentials: "include" });
      if (!response.ok) throw new Error(`Could not obtain a CSRF token (HTTP ${response.status})`);
      csrfToken = ((await response.json()) as { csrf_token: string }).csrf_token;
      return csrfToken;
    })().finally(() => {
      csrfInFlight = null;
    });
    return csrfInFlight;
  }

  function withCsrf(request: Request, token: string): Request {
    const headers = new Headers(request.headers);
    headers.set("X-CSRF-Token", token);
    return new Request(request, { headers });
  }

  function refreshSession(): Promise<boolean> {
    refreshInFlight ??= (async () => {
      try {
        const token = await getCsrf();
        const response = await fetchImpl(`${baseUrl}/api/auth/refresh`, {
          method: "POST",
          credentials: "include",
          headers: { "X-CSRF-Token": token },
        });
        return response.ok;
      } catch {
        return false;
      }
    })().finally(() => {
      refreshInFlight = null;
    });
    return refreshInFlight;
  }

  async function send(request: Request): Promise<Response> {
    if (!UNSAFE_METHODS.has(request.method)) return fetchImpl(request);
    return fetchImpl(withCsrf(request, await getCsrf()));
  }

  async function isCsrfRejection(response: Response): Promise<boolean> {
    if (response.status !== 403) return false;
    const body = (await response
      .clone()
      .json()
      .catch(() => null)) as { detail?: string } | null;
    return Boolean(body?.detail?.includes("CSRF"));
  }

  return async function authFetch(input: Request): Promise<Response> {
    const pristine = input.clone();
    let response = await send(input.clone());

    if (UNSAFE_METHODS.has(input.method) && (await isCsrfRejection(response))) {
      await getCsrf(true); // cookie expired or was cleared: get a fresh token and retry once
      response = await send(pristine.clone());
    }

    const path = new URL(input.url).pathname;
    if (response.status === 401 && !NO_REFRESH_PATHS.includes(path)) {
      if (await refreshSession()) {
        response = await send(pristine.clone());
      }
    }
    return response;
  };
}
