"use client";

import Link from "next/link";

import { RequireAuth } from "@/components/auth/require-auth";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { isOwner } from "@/lib/auth";

export function Dashboard() {
  return (
    <RequireAuth>
      {(me) => (
        <section className="flex flex-col gap-6">
          <div className="flex flex-col gap-1">
            <h1 className="text-2xl font-semibold tracking-tight">Welcome, {me.user.name}</h1>
            <p className="text-sm text-muted-foreground">
              {me.org.name} <span className="font-mono">({me.org.slug})</span>
            </p>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  Your account <Badge variant="secondary">{me.user.role.toLowerCase()}</Badge>
                </CardTitle>
                <CardDescription>{me.user.email}</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-2 text-sm">
                <p className="text-muted-foreground">
                  Voice interviews: {me.policy.can_use_voice ? "allowed" : "not allowed"} · Coding rounds:{" "}
                  {me.policy.can_use_coding ? "allowed" : "not allowed"} · Prompt editing:{" "}
                  {me.policy.can_edit_prompts ? "allowed" : "not allowed"}
                </p>
                <Link
                  href="/app/account"
                  className={buttonVariants({ variant: "outline", size: "sm", className: "w-fit" })}
                >
                  Account settings
                </Link>
              </CardContent>
            </Card>
            {isOwner(me) && (
              <Card>
                <CardHeader>
                  <CardTitle>Organization</CardTitle>
                  <CardDescription>
                    Approve members, manage groups and invitations, review the audit log.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <Link href="/admin" className={buttonVariants({ size: "sm" })}>
                    Open admin
                  </Link>
                </CardContent>
              </Card>
            )}
          </div>
        </section>
      )}
    </RequireAuth>
  );
}
