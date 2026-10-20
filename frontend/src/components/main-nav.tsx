"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { Button, buttonVariants } from "@/components/ui/button";
import { isOwner, useLogout, useMe } from "@/lib/auth";

export function MainNav() {
  const { data: me } = useMe();
  const logout = useLogout();
  const router = useRouter();
  const link = buttonVariants({ variant: "ghost", size: "sm" });

  return (
    <nav aria-label="Main" className="flex items-center gap-1">
      {me && me.user.status === "ACTIVE" && (
        <Link href="/app" className={link}>
          Dashboard
        </Link>
      )}
      {me && me.user.status === "ACTIVE" && (
        <Link href="/app/knowledge" className={link}>
          Knowledge
        </Link>
      )}
      {me && me.user.status === "ACTIVE" && (
        <Link href="/app/role-packs" className={link}>
          Role packs
        </Link>
      )}
      {me && me.user.status === "ACTIVE" && (
        <Link href="/app/questions" className={link}>
          Questions
        </Link>
      )}
      {me && me.user.status === "ACTIVE" && (
        <Link href="/app/interviews" className={link}>
          Interviews
        </Link>
      )}
      {me && me.user.status === "ACTIVE" && (
        <Link href="/app/llm" className={link}>
          AI models
        </Link>
      )}
      {isOwner(me) && (
        <Link href="/admin" className={link}>
          Admin
        </Link>
      )}
      <Link href="/healthz" className={link}>
        Status
      </Link>
      {me ? (
        <>
          <Link href="/app/account" className={link} title={me.user.email}>
            {me.user.name}
          </Link>
          <Button
            variant="outline"
            size="sm"
            disabled={logout.isPending}
            onClick={() => logout.mutate(undefined, { onSettled: () => router.push("/login") })}
          >
            Sign out
          </Button>
        </>
      ) : (
        me === null && (
          <Link href="/login" className={buttonVariants({ size: "sm" })}>
            Sign in
          </Link>
        )
      )}
    </nav>
  );
}
