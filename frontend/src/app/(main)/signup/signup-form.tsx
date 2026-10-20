"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { FormField } from "@/components/form-field";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import { emailSchema, orgSlugSchema, passwordSchema } from "@/lib/validation";

const schema = z
  .object({
    org_slug: orgSlugSchema,
    name: z.string().trim().min(1, "Enter your name").max(200),
    email: emailSchema,
    password: passwordSchema,
    confirm: z.string(),
  })
  .refine((v) => v.password === v.confirm, { path: ["confirm"], message: "Passwords do not match" });
type Values = z.infer<typeof schema>;

export function SignupForm() {
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { org_slug: "", name: "", email: "", password: "", confirm: "" },
  });
  const { errors, isSubmitting } = form.formState;

  async function onSubmit({ org_slug, name, email, password }: Values) {
    setError(null);
    try {
      const result = unwrap(await api.POST("/api/auth/signup", { body: { org_slug, name, email, password } }));
      setDone(result.message);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed.");
    }
  }

  if (done) {
    return (
      <Alert>
        <AlertTitle>Request sent</AlertTitle>
        <AlertDescription>
          <p>{done}</p>
          <Link href="/login" className="font-medium text-foreground underline-offset-4 hover:underline">
            Go to sign in
          </Link>
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <form className="flex flex-col gap-4" noValidate onSubmit={form.handleSubmit(onSubmit)}>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <FormField
        id="org_slug"
        label="Organization ID"
        error={errors.org_slug?.message}
        hint="Ask your administrator for it."
      >
        <Input
          id="org_slug"
          autoComplete="organization"
          aria-invalid={!!errors.org_slug}
          {...form.register("org_slug")}
        />
      </FormField>
      <FormField id="name" label="Full name" error={errors.name?.message}>
        <Input id="name" autoComplete="name" aria-invalid={!!errors.name} {...form.register("name")} />
      </FormField>
      <FormField id="email" label="Work email" error={errors.email?.message}>
        <Input id="email" type="email" autoComplete="email" aria-invalid={!!errors.email} {...form.register("email")} />
      </FormField>
      <FormField id="password" label="Password" error={errors.password?.message} hint="At least 12 characters.">
        <Input
          id="password"
          type="password"
          autoComplete="new-password"
          aria-invalid={!!errors.password}
          {...form.register("password")}
        />
      </FormField>
      <FormField id="confirm" label="Confirm password" error={errors.confirm?.message}>
        <Input
          id="confirm"
          type="password"
          autoComplete="new-password"
          aria-invalid={!!errors.confirm}
          {...form.register("confirm")}
        />
      </FormField>
      <Button type="submit" disabled={isSubmitting}>
        {isSubmitting ? "Sending…" : "Request access"}
      </Button>
    </form>
  );
}
