"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { isOwner, useMe, type Me } from "@/lib/auth";

interface RequireAuthProps {
  children: (me: Me) => ReactNode;
  /** Pending (unapproved) users are sent to /pending. */
  requireActive?: boolean;
  requireOwner?: boolean;
}

/** Client-side guard: the API is the real authority; this only routes users to the right page. */
export function RequireAuth({ children, requireActive = true, requireOwner = false }: RequireAuthProps) {
  const { data: me, isPending, isError, error } = useMe();
  const router = useRouter();
  const pathname = usePathname();

  const signedOut = !isPending && !isError && me === null;
  const pending = Boolean(me && requireActive && me.user.status === "PENDING");

  useEffect(() => {
    if (signedOut) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    else if (pending) router.replace("/pending");
  }, [signedOut, pending, pathname, router]);

  if (isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Could not load your account</AlertTitle>
        <AlertDescription>{error.message}</AlertDescription>
      </Alert>
    );
  }
  if (isPending || !me || pending) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }
  if (requireOwner && !isOwner(me)) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Owners only</AlertTitle>
        <AlertDescription>Only organization owners can open this page.</AlertDescription>
      </Alert>
    );
  }
  return <>{children(me)}</>;
}
