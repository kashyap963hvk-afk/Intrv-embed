"use client";

import { useQuery } from "@tanstack/react-query";

import { buttonVariants } from "@/components/ui/button";
import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import { env } from "@/lib/env";
import { googleStartUrl, type GoogleStart } from "@/lib/google-sign-in";

export function useGoogleSignIn(): boolean {
  const query = useQuery({
    queryKey: ["auth", "google"],
    queryFn: async () => unwrap(await api.GET("/api/auth/google")),
    staleTime: 5 * 60_000,
  });
  return query.data?.enabled === true;
}

/** "Continue with Google": a link, so the browser goes through Google and comes back signed in. */
export function GoogleSignInButton(props: GoogleStart) {
  return (
    <a
      href={googleStartUrl(env.apiBaseUrl, props)}
      className={buttonVariants({ variant: "outline", className: "w-full" })}
    >
      <svg aria-hidden viewBox="0 0 24 24" className="size-4">
        <path
          fill="currentColor"
          d="M21.35 11.1H12v2.98h5.35c-.23 1.5-1.68 4.4-5.35 4.4a5.9 5.9 0 0 1 0-11.8c1.84 0 3.07.78 3.78 1.46l2.58-2.49A9.4 9.4 0 0 0 12 2.6a9.4 9.4 0 1 0 0 18.8c5.43 0 9.03-3.82 9.03-9.2 0-.62-.07-1.09-.15-1.56Z"
        />
      </svg>
      Continue with Google
    </a>
  );
}
