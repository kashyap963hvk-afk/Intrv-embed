"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { FormField } from "@/components/form-field";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api/client";
import { ApiError, unwrap } from "@/lib/api/problem";
import { ME_QUERY_KEY } from "@/lib/auth";
import { emailSchema } from "@/lib/validation";

const schema = z.object({
  email: emailSchema,
  password: z.string().min(1, "Enter your password"),
});
type Values = z.infer<typeof schema>;

function safeNext(next: string | null): string {
  // Only same-site relative paths: never redirect to another origin.
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/app";
}

export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [orgChoices, setOrgChoices] = useState<{ slug: string; name: string }[] | null>(null);
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: { email: "", password: "" } });

  async function submit(values: Values, orgSlug?: string) {
    setError(null);
    try {
      const me = unwrap(await api.POST("/api/auth/login", { body: { ...values, org_slug: orgSlug ?? null } }));
      queryClient.setQueryData(ME_QUERY_KEY, me);
      router.push(me.user.status === "PENDING" ? "/pending" : safeNext(params.get("next")));
    } catch (e) {
      if (e instanceof ApiError && e.problem.status === 409 && e.problem.organizations) {
        setOrgChoices(e.problem.organizations);
        return;
      }
      setError(e instanceof Error ? e.message : "Sign-in failed.");
    }
  }

  const { errors, isSubmitting } = form.formState;

  return (
    <form className="flex flex-col gap-4" noValidate onSubmit={form.handleSubmit((values) => submit(values))}>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <FormField id="email" label="Email" error={errors.email?.message}>
        <Input id="email" type="email" autoComplete="email" aria-invalid={!!errors.email} {...form.register("email")} />
      </FormField>
      <FormField id="password" label="Password" error={errors.password?.message}>
        <Input
          id="password"
          type="password"
          autoComplete="current-password"
          aria-invalid={!!errors.password}
          {...form.register("password")}
        />
      </FormField>

      {orgChoices ? (
        <fieldset className="flex flex-col gap-2 rounded-md border p-3">
          <legend className="px-1 text-sm font-medium">Choose an organization</legend>
          {orgChoices.map((org) => (
            <Button
              key={org.slug}
              type="button"
              variant="outline"
              disabled={isSubmitting}
              onClick={form.handleSubmit((values) => submit(values, org.slug))}
            >
              {org.name} <span className="text-muted-foreground">({org.slug})</span>
            </Button>
          ))}
        </fieldset>
      ) : (
        <Button type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Signing in…" : "Sign in"}
        </Button>
      )}
      <p className="text-sm text-muted-foreground">
        New here?{" "}
        <Link href="/signup" className="font-medium text-foreground underline-offset-4 hover:underline">
          Request access
        </Link>
      </p>
    </form>
  );
}
