/** Sign in with Google: a full-page navigation to the API, which redirects to Google and back to the app. */
export interface GoogleStart {
  next?: string | null;
  invite?: string | null;
  org?: string | null;
}

export function googleStartUrl(apiBaseUrl: string, { next, invite, org }: GoogleStart): string {
  const query = new URLSearchParams();
  if (next && next.startsWith("/") && !next.startsWith("//")) query.set("next", next);
  if (invite) query.set("invite", invite);
  if (org) query.set("org", org);
  const suffix = query.toString();
  return `${apiBaseUrl}/api/auth/google/start${suffix ? `?${suffix}` : ""}`;
}
